#!/usr/bin/env python3

import sys
import json
import threading
import time
from collections import deque
from datetime import datetime

import gi
import websocket

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")

from gi.repository import Gst, GstWebRTC, GstSdp, GLib


Gst.init(None)


class WebRTCSender:

    def __init__(self):

        self.loop = GLib.MainLoop()

        # ============================================================
        # WebSocket
        # ============================================================

        self.ws_url = "ws://127.0.0.1:8765"
        self.ws = None
        self.ws_connected = threading.Event()

        # ============================================================
        # Software timestamp / frame statistics
        # ============================================================

        self.frame_id = 0

        self.report_frame_count = 0
        self.last_report_time = time.monotonic()

        # ============================================================
        # RTP timestamp -> source timestamp mapping
        #
        # rVFC() on the browser exposes the RTP timestamp belonging
        # to the exact decoded video frame.  We therefore record the
        # RTP timestamp after rtph264pay and associate it with the
        # source-buffer wall-clock timestamp.
        # ============================================================

        # Source frames waiting to be paired with an encoded frame.
        #
        # Pairing is by ORDER, not by PTS.  x264enc rewrites the buffer PTS
        # (it adds a constant base), so the source PTS and the PTS observed at
        # rtph264pay live in two different domains and can never be equal.
        # The offset between the two domains is measured from the first pair
        # and re-anchored on every frame, which also detects frames dropped
        # upstream instead of silently shifting the pairing by one frame.
        self.source_frames = deque()

        self.rtp_mapping_queue = []

        self.last_rtp_timestamp = None

        self.pts_offset = None

        # Diagnostics for the periodic [MAP] report.
        self.map_matched = 0
        self.map_missed = 0
        self.offset_adjustments = 0
        self.offset_last_delta = 0
        self.skipped_source_frames = 0

        self.rtp_mapping_lock = threading.Lock()
        # ============================================================
        # GStreamer pipeline
        #
        # 这里保持 WebRTC 视频链路。
        #
        # timestamp_probe 只负责：
        #
        #   1. 统计帧数
        #   2. 记录 Python 墙上时钟
        #   3. 记录 monotonic 时间
        #   4. 记录 GStreamer PTS
        #
        # 不修改 buffer
        # 不 map buffer
        # 不使用 OpenCV
        #
        # ============================================================

        pipeline_description = """
            v4l2src device=/dev/video0 io-mode=mmap
            !
            image/jpeg,width=1920,height=1080,framerate=30/1
            !
            jpegdec
            !
            videoconvert
            !
            video/x-raw,format=BGR
            !
            identity name=timestamp_probe
            !
            videoconvert
            !
            video/x-raw,format=I420
            !
            x264enc
                tune=zerolatency
                speed-preset=ultrafast
                bitrate=4000
                key-int-max=30
            !
            h264parse
            !
            rtph264pay
                config-interval=1
                pt=96
            !
            application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000
            !
            queue name=rtp_queue
                max-size-buffers=32
                max-size-bytes=0
                max-size-time=0
        """

        self.pipeline = Gst.parse_launch(
            pipeline_description
        )

        # ============================================================
        # Timestamp probe
        # ============================================================

        self.timestamp_probe = (
            self.pipeline.get_by_name(
                "timestamp_probe"
            )
        )

        if not self.timestamp_probe:
            raise RuntimeError(
                "Failed to find timestamp_probe"
            )

        timestamp_src_pad = (
            self.timestamp_probe.get_static_pad(
                "src"
            )
        )

        if not timestamp_src_pad:
            raise RuntimeError(
                "Failed to get timestamp_probe src pad"
            )

        timestamp_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self.on_video_buffer
        )

        # ============================================================
        # WebRTC
        # ============================================================

        webrtc_factory = Gst.ElementFactory.make(
            "webrtcbin",
            "webrtc"
        )

        if not webrtc_factory:
            raise RuntimeError(
                "Failed to create webrtcbin"
            )

        webrtc_factory.set_property(
            "bundle-policy",
            "max-bundle"
        )

        self.pipeline.add(
            webrtc_factory
        )

        self.webrtc = webrtc_factory

        # ============================================================
        # RTP queue
        # ============================================================

        rtp_queue = self.pipeline.get_by_name(
            "rtp_queue"
        )

        if not rtp_queue:
            raise RuntimeError(
                "Failed to find RTP queue"
            )

        rtp_src_pad = rtp_queue.get_static_pad(
            "src"
        )

        rtp_sink_pad = rtp_queue.get_static_pad(
            "sink"
        )

        if not rtp_sink_pad:
            raise RuntimeError(
                "Failed to get RTP queue sink pad"
            )

        rtp_sink_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self.on_rtp_buffer
        )

        # ============================================================
        # WebRTC signals
        # ============================================================

        self.webrtc.connect(
            "on-negotiation-needed",
            self.on_negotiation_needed
        )

        self.webrtc.connect(
            "on-ice-candidate",
            self.on_ice_candidate
        )

        # ============================================================
        # WebRTC sink pad
        # ============================================================

        webrtc_sink_pad = (
            self.webrtc.request_pad_simple(
                "sink_0"
            )
        )

        if not webrtc_sink_pad:
            raise RuntimeError(
                "Failed to request webrtc sink pad"
            )

        print(
            "[WebRTC] Requested sink pad:",
            webrtc_sink_pad.get_name()
        )

        # ============================================================
        # RTP -> WebRTC
        # ============================================================

        link_result = rtp_src_pad.link(
            webrtc_sink_pad
        )

        print(
            "[WebRTC] RTP -> WebRTC link:",
            link_result.value_nick
        )

        if link_result != Gst.PadLinkReturn.OK:
            raise RuntimeError(
                "Failed to link RTP to webrtcbin"
            )

        # ============================================================
        # GStreamer bus
        # ============================================================

        bus = self.pipeline.get_bus()

        bus.add_signal_watch()

        bus.connect(
            "message",
            self.on_bus_message
        )

    # =================================================================
    # Software timestamp probe
    # =================================================================

    def on_video_buffer(
        self,
        pad,
        info
    ):

        buffer = info.get_buffer()

        if not buffer:
            return Gst.PadProbeReturn.OK

        # ============================================================
        # Frame ID
        # ============================================================

        self.frame_id += 1

        # ============================================================
        # Source wall-clock timestamp
        #
        # This is the time when the decoded camera frame reaches this
        # GStreamer pad.  It is NOT claimed to be the sensor exposure
        # timestamp.
        # ============================================================

        wall_time_ns = time.time_ns()

        wall_datetime = datetime.fromtimestamp(
            wall_time_ns / 1_000_000_000
        )

        wall_timestamp = (
            f"{wall_datetime.strftime('%Y-%m-%d %H:%M:%S')}."
            f"{wall_datetime.microsecond // 1000:03d}"
        )

        # ============================================================
        # Monotonic timestamp
        # ============================================================

        monotonic_ns = time.monotonic_ns()

        # ============================================================
        # GStreamer PTS
        # ============================================================

        pts = buffer.pts

        if pts == Gst.CLOCK_TIME_NONE:
            pts_ms = None
        else:
            pts_ms = pts / Gst.MSECOND

        # ============================================================
        # Save source timestamp keyed by the exact GStreamer PTS.
        #
        # rtph264pay output buffers retain the media PTS, so the RTP
        # probe can associate its real RTP timestamp with this record.
        # ============================================================

        if pts != Gst.CLOCK_TIME_NONE:
            with self.rtp_mapping_lock:
                self.source_frames.append(
                    {
                        "frame_id": self.frame_id,
                        "capture_time_ns": wall_time_ns,
                        "capture_time_ms": wall_time_ns / 1_000_000.0,
                        "capture_timestamp": wall_timestamp,
                        "monotonic_ns": monotonic_ns,
                        "pts_ns": pts,
                        "pts_ms": pts_ms,
                    }
                )

                # Keep memory bounded.  A dropped record re-anchors the
                # pairing offset on the next encoded frame.
                while len(self.source_frames) > 600:
                    self.source_frames.popleft()
                    self.skipped_source_frames += 1

        # ============================================================
        # FPS
        # ============================================================

        self.report_frame_count += 1

        elapsed = (
            time.monotonic()
            - self.last_report_time
        )

        if elapsed >= 1.0:

            fps = (
                self.report_frame_count
                / elapsed
            )

            print(
                f"[SOURCE] "
                f"Frame={self.frame_id:08d} "
                f"Time={wall_timestamp} "
                f"PTS={pts_ms if pts_ms is not None else 'NONE'} "
                f"MonoNS={monotonic_ns} "
                f"FPS={fps:.2f}"
            )

            self.report_frame_count = 0
            self.last_report_time = time.monotonic()

        return Gst.PadProbeReturn.OK

    # =================================================================
    # RTP timestamp probe
    # =================================================================

    def on_rtp_buffer(
        self,
        pad,
        info
    ):

        buffer = info.get_buffer()

        if not buffer:
            return Gst.PadProbeReturn.OK

        # ============================================================
        # Read the RTP header.
        #
        # RTP timestamp is bytes 4..7 of the fixed 12-byte header,
        # in network byte order.  We deliberately read the actual RTP
        # packet instead of deriving the timestamp from GStreamer PTS.
        # ============================================================

        success, map_info = buffer.map(
            Gst.MapFlags.READ
        )

        if not success:
            return Gst.PadProbeReturn.OK

        try:
            if len(map_info.data) < 12:
                return Gst.PadProbeReturn.OK

            rtp_timestamp = int.from_bytes(
                bytes(map_info.data[4:8]),
                byteorder="big",
                signed=False
            )

        finally:
            buffer.unmap(map_info)

        # ============================================================
        # Associate the RTP timestamp with the source frame.
        # Multiple RTP packets belonging to one H.264 frame have the
        # same RTP timestamp, so only the first packet is reported.
        # ============================================================

        pts = buffer.pts

        if pts == Gst.CLOCK_TIME_NONE:
            return Gst.PadProbeReturn.OK

        with self.rtp_mapping_lock:

            # One H.264 frame spans many RTP packets and they all carry the
            # same RTP timestamp, so only the frame's first packet is paired.
            if rtp_timestamp == self.last_rtp_timestamp:
                return Gst.PadProbeReturn.OK

            self.last_rtp_timestamp = rtp_timestamp

            if not self.source_frames:
                self.map_missed += 1
                return Gst.PadProbeReturn.OK

            if self.pts_offset is None:
                # Bootstrap: the oldest unpaired source frame is this frame.
                source = self.source_frames.popleft()
                self.pts_offset = pts - source["pts_ns"]
            else:
                # Advance to the source frame this PTS belongs to.  Records
                # that were skipped upstream are dropped here so the pairing
                # stays aligned.
                target = pts - self.pts_offset

                while (
                    len(self.source_frames) > 1
                    and self.source_frames[1]["pts_ns"] <= target
                ):
                    self.source_frames.popleft()
                    self.skipped_source_frames += 1

                source = self.source_frames.popleft()

                observed = pts - source["pts_ns"]

                if observed != self.pts_offset:
                    self.offset_last_delta = observed - self.pts_offset
                    self.offset_adjustments += 1
                    self.pts_offset = observed

            self.map_matched += 1

            mapping = {
                "type": "frame_rtp_mapping",
                "rtp_timestamp": rtp_timestamp,
                "frame_id": source["frame_id"],
                "capture_time_ns": source["capture_time_ns"],
                "capture_time_ms": source["capture_time_ms"],
                "capture_timestamp": source["capture_timestamp"],
                "monotonic_ns": source["monotonic_ns"],
                "pts_ns": source["pts_ns"],
                "pts_ms": source["pts_ms"],
            }

            self.rtp_mapping_queue.append(mapping)

        return Gst.PadProbeReturn.OK

    # =================================================================
    # Send RTP mappings in small batches
    # =================================================================

    def report_mapping_stats(self):

        with self.rtp_mapping_lock:
            matched = self.map_matched
            missed = self.map_missed
            offset = self.pts_offset
            adjustments = self.offset_adjustments
            last_delta = self.offset_last_delta
            skipped = self.skipped_source_frames
            pending = len(self.source_frames)

        total = matched + missed

        rate = (100.0 * matched / total) if total else 0.0

        print(
            f"[MAP] matched={matched} missed={missed} "
            f"rate={rate:.1f}% offset={offset} "
            f"offset_fixes={adjustments} last_delta={last_delta} "
            f"skipped={skipped} pending={pending}"
        )

        return True

    def flush_rtp_mappings(self):

        if not self.ws_connected.is_set():
            return True

        with self.rtp_mapping_lock:

            if not self.rtp_mapping_queue:
                return True

            batch = self.rtp_mapping_queue[:20]
            del self.rtp_mapping_queue[:20]

        self.send_signaling_message(
            {
                "type": "frame_rtp_mapping_batch",
                "items": batch
            }
        )

        return True

    # =================================================================
    # WebSocket
    # =================================================================

    def start_websocket(self):

        print(
            "[SIGNALING] Connecting to",
            self.ws_url
        )

        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self.on_ws_open,
            on_message=self.on_ws_message,
            on_error=self.on_ws_error,
            on_close=self.on_ws_close,
        )

        self.ws_thread = threading.Thread(
            target=self.ws.run_forever,
            daemon=True
        )

        self.ws_thread.start()

    def on_ws_open(
        self,
        ws
    ):

        print(
            "[SIGNALING] WebSocket connected"
        )

        self.ws_connected.set()

    def on_ws_message(
        self,
        ws,
        message
    ):

        print(
            "[SIGNALING] Received message"
        )

        try:

            data = json.loads(
                message
            )

        except json.JSONDecodeError as e:

            print(
                "[SIGNALING] Invalid JSON:",
                e
            )

            return

        message_type = data.get(
            "type"
        )

        print(
            "[SIGNALING] Message type:",
            message_type
        )

        # ------------------------------------------------------------
        # Clock synchronisation (NTP-style, initiated by the browser)
        #
        # The browser and this sender are two different machines with two
        # independent clocks, so a time recorded here means nothing on the
        # browser's clock until the difference between them is known.
        #
        # The browser sends t1 (its clock).  We answer with t2, the time we
        # received it, and t3, the time we send the reply.  The browser then
        # solves for offset = sender_clock - browser_clock and subtracts it
        # from every capture timestamp we send.
        #
        # Without this, every latency is wrong by the entire clock difference
        # (measured ~194 ms on this setup) -- and that difference is not even
        # constant, because the two machines are disciplined by two different
        # time sources.
        # ------------------------------------------------------------

        if message_type == "clock_sync":

            t1 = data.get("t1")

            if t1 is None:
                return

            self.send_signaling_message(
                {
                    "type": "clock_sync_reply",
                    "t1": t1,
                    "t2": time.time() * 1000.0,
                    "t3": time.time() * 1000.0,
                }
            )

            return

        # ------------------------------------------------------------
        # Answer
        # ------------------------------------------------------------

        if message_type == "answer":

            sdp = data.get(
                "sdp"
            )

            if not sdp:

                print(
                    "[SIGNALING] Answer has no SDP"
                )

                return

            GLib.idle_add(
                self.set_remote_answer,
                sdp
            )

        # ------------------------------------------------------------
        # ICE candidate
        # ------------------------------------------------------------

        elif message_type == "candidate":

            candidate = data.get(
                "candidate"
            )

            sdp_mline_index = data.get(
                "sdpMLineIndex"
            )

            sdp_mid = data.get(
                "sdpMid"
            )

            if candidate is None:
                return

            GLib.idle_add(
                self.add_remote_candidate,
                candidate,
                sdp_mline_index,
                sdp_mid
            )

    def on_ws_error(
        self,
        ws,
        error
    ):

        print(
            "[SIGNALING] WebSocket error:",
            error
        )

    def on_ws_close(
        self,
        ws,
        close_status_code,
        close_msg
    ):

        print(
            "[SIGNALING] WebSocket closed:",
            close_status_code,
            close_msg
        )

        self.ws_connected.clear()

    def send_signaling_message(
        self,
        data
    ):

        if not self.ws_connected.is_set():

            print(
                "[SIGNALING] WebSocket not connected"
            )

            return

        try:

            message = json.dumps(
                data
            )

            self.ws.send(
                message
            )

        except Exception as e:

            print(
                "[SIGNALING] Send failed:",
                e
            )

    # =================================================================
    # WebRTC negotiation
    # =================================================================

    def on_negotiation_needed(
        self,
        element
    ):

        print(
            "[WebRTC] Negotiation needed"
        )

        promise = Gst.Promise.new_with_change_func(
            self.on_offer_created,
            element
        )

        element.emit(
            "create-offer",
            None,
            promise
        )

    def on_offer_created(
        self,
        promise,
        element
    ):

        reply = promise.get_reply()

        offer = reply.get_value(
            "offer"
        )

        print(
            "[WebRTC] SDP offer created"
        )

        set_local_promise = Gst.Promise.new()

        element.emit(
            "set-local-description",
            offer,
            set_local_promise
        )

        text = offer.sdp.as_text()

        print(
            "\n========== SDP OFFER ==========\n"
        )

        print(text)

        print(
            "========== END SDP OFFER ======\n"
        )

        self.send_signaling_message(
            {
                "type": "offer",
                "sdp": text
            }
        )

        print(
            "[SIGNALING] SDP offer sent"
        )

    # =================================================================
    # Remote SDP
    # =================================================================

    def set_remote_answer(
        self,
        sdp_text
    ):

        print(
            "[WebRTC] Setting remote SDP answer"
        )

        result, sdp_message = (
            GstSdp.sdp_message_new_from_text(
                sdp_text
            )
        )

        if result != GstSdp.SDPResult.OK:

            print(
                "[WebRTC] Failed to parse SDP answer"
            )

            return False

        answer = (
            GstWebRTC.WebRTCSessionDescription.new(
                GstWebRTC.WebRTCSDPType.ANSWER,
                sdp_message
            )
        )

        promise = Gst.Promise.new()

        self.webrtc.emit(
            "set-remote-description",
            answer,
            promise
        )

        print(
            "[WebRTC] Remote SDP answer set"
        )

        return False

    # =================================================================
    # ICE
    # =================================================================

    def on_ice_candidate(
        self,
        element,
        mlineindex,
        candidate
    ):

        print(
            "[WebRTC] ICE candidate:",
            candidate
        )

        self.send_signaling_message(
            {
                "type": "candidate",
                "candidate": candidate,
                "sdpMLineIndex": mlineindex,
                "sdpMid": "video0"
            }
        )

    def add_remote_candidate(
        self,
        candidate,
        sdp_mline_index,
        sdp_mid
    ):

        print(
            "[WebRTC] Adding remote ICE candidate"
        )

        try:

            self.webrtc.emit(
                "add-ice-candidate",
                sdp_mline_index,
                candidate
            )

            print(
                "[WebRTC] Remote ICE candidate added"
            )

        except Exception as e:

            print(
                "[WebRTC] Failed to add remote ICE candidate:",
                e
            )

        return False

    # =================================================================
    # GStreamer bus
    # =================================================================

    def on_bus_message(
        self,
        bus,
        message
    ):

        msg_type = message.type

        if msg_type == Gst.MessageType.ERROR:

            error, debug = (
                message.parse_error()
            )

            print(
                "[GStreamer ERROR]"
            )

            print(error)

            if debug:
                print(debug)

            self.loop.quit()

        elif msg_type == Gst.MessageType.WARNING:

            warning, debug = (
                message.parse_warning()
            )

            print(
                "[GStreamer WARNING]"
            )

            print(warning)

            if debug:
                print(debug)

        elif msg_type == Gst.MessageType.EOS:

            print(
                "[GStreamer] EOS"
            )

            self.loop.quit()

        elif msg_type == Gst.MessageType.STATE_CHANGED:

            if message.src == self.pipeline:

                (
                    old_state,
                    new_state,
                    pending
                ) = message.parse_state_changed()

                print(
                    f"[GStreamer] Pipeline: "
                    f"{old_state.value_nick} -> "
                    f"{new_state.value_nick}"
                )

        elif msg_type == Gst.MessageType.LATENCY:

            print(
                "[GStreamer] LATENCY"
            )

    # =================================================================
    # Start
    # =================================================================

    def start(self):

        print(
            "[SIGNALING] Starting WebSocket..."
        )

        self.start_websocket()

        # Flush RTP timestamp mappings at 20 Hz.  This is only the
        # metadata transport; video itself remains untouched.
        GLib.timeout_add(
            50,
            self.flush_rtp_mappings
        )

        GLib.timeout_add(
            1000,
            self.report_mapping_stats
        )

        print(
            "[SIGNALING] Waiting for WebSocket connection..."
        )

        if not self.ws_connected.wait(
            timeout=10
        ):

            print(
                "[SIGNALING] WebSocket connection timeout"
            )

            sys.exit(1)

        print(
            "[SIGNALING] WebSocket ready"
        )

        print(
            "[GStreamer] Starting pipeline..."
        )

        ret = self.pipeline.set_state(
            Gst.State.PLAYING
        )

        if ret == Gst.StateChangeReturn.FAILURE:

            print(
                "[GStreamer] Failed to start pipeline"
            )

            sys.exit(1)

        self.loop.run()

    # =================================================================
    # Stop
    # =================================================================

    def stop(self):

        print(
            "[GStreamer] Stopping pipeline..."
        )

        self.pipeline.set_state(
            Gst.State.NULL
        )

        if self.ws:

            try:
                self.ws.close()

            except Exception:
                pass


# =====================================================================
# Main
# =====================================================================

if __name__ == "__main__":

    sender = WebRTCSender()

    try:

        sender.start()

    except KeyboardInterrupt:

        print(
            "\nStopping..."
        )

    finally:

        sender.stop()
