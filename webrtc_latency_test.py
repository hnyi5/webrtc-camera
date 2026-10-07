#!/usr/bin/env python3

import sys
import json
import threading

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

        # WebSocket
        self.ws_url = "ws://127.0.0.1:8765"
        self.ws = None
        self.ws_connected = threading.Event()

        # GStreamer pipeline
        pipeline_description = """
            v4l2src device=/dev/video0 io-mode=mmap
                !
            video/x-h264,width=1920,height=1080,framerate=30/1
                !
            h264parse
                !
            rtph264pay config-interval=1 pt=96
                !
            application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000
                !
            queue name=rtp_queue
        """

        self.pipeline = Gst.parse_launch(pipeline_description)

        # webrtcbin
        webrtc_factory = Gst.ElementFactory.make("webrtcbin", "webrtc")

        if not webrtc_factory:
            raise RuntimeError("Failed to create webrtcbin")

        webrtc_factory.set_property("bundle-policy", "max-bundle")

        self.pipeline.add(webrtc_factory)
        self.webrtc = webrtc_factory

        # RTP queue
        rtp_queue = self.pipeline.get_by_name("rtp_queue")
        rtp_src_pad = rtp_queue.get_static_pad("src")

        # 先连接 WebRTC 信号
        self.webrtc.connect(
            "on-negotiation-needed",
            self.on_negotiation_needed
        )

        self.webrtc.connect(
            "on-ice-candidate",
            self.on_ice_candidate
        )

        # 申请 WebRTC sink pad
        webrtc_sink_pad = self.webrtc.request_pad_simple("sink_0")

        if not webrtc_sink_pad:
            raise RuntimeError("Failed to request webrtc sink pad")

        print(
            "[WebRTC] Requested sink pad:",
            webrtc_sink_pad.get_name()
        )

        # RTP -> WebRTC
        link_result = rtp_src_pad.link(webrtc_sink_pad)

        print(
            "[WebRTC] RTP -> WebRTC link:",
            link_result.value_nick
        )

        if link_result != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link RTP to webrtcbin")

        # GStreamer bus
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

    # ============================================================
    # WebSocket
    # ============================================================

    def start_websocket(self):
        print("[SIGNALING] Connecting to", self.ws_url)

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

    def on_ws_open(self, ws):
        print("[SIGNALING] WebSocket connected")
        self.ws_connected.set()

    def on_ws_message(self, ws, message):
        print("[SIGNALING] Received message")

        try:
            data = json.loads(message)
        except json.JSONDecodeError as e:
            print("[SIGNALING] Invalid JSON:", e)
            return

        message_type = data.get("type")

        print("[SIGNALING] Message type:", message_type)

        if message_type == "answer":
            sdp = data.get("sdp")

            if not sdp:
                print("[SIGNALING] Answer has no SDP")
                return

            # 切回 GLib MainLoop 线程处理 GStreamer
            GLib.idle_add(
                self.set_remote_answer,
                sdp
            )

        elif message_type == "candidate":
            candidate = data.get("candidate")
            sdp_mline_index = data.get("sdpMLineIndex")
            sdp_mid = data.get("sdpMid")

            if candidate is None:
                return

            GLib.idle_add(
                self.add_remote_candidate,
                candidate,
                sdp_mline_index,
                sdp_mid
            )

    def on_ws_error(self, ws, error):
        print("[SIGNALING] WebSocket error:", error)

    def on_ws_close(self, ws, close_status_code, close_msg):
        print(
            "[SIGNALING] WebSocket closed:",
            close_status_code,
            close_msg
        )

        self.ws_connected.clear()

    def send_signaling_message(self, data):
        if not self.ws_connected.is_set():
            print("[SIGNALING] WebSocket not connected")
            return

        try:
            message = json.dumps(data)
            self.ws.send(message)

        except Exception as e:
            print("[SIGNALING] Send failed:", e)

    # ============================================================
    # WebRTC negotiation
    # ============================================================

    def on_negotiation_needed(self, element):
        print("[WebRTC] Negotiation needed")

        promise = Gst.Promise.new_with_change_func(
            self.on_offer_created,
            element
        )

        element.emit(
            "create-offer",
            None,
            promise
        )

    def on_offer_created(self, promise, element):
        reply = promise.get_reply()

        offer = reply.get_value("offer")

        print("[WebRTC] SDP offer created")

        # 设置本地 SDP
        set_local_promise = Gst.Promise.new()

        element.emit(
            "set-local-description",
            offer,
            set_local_promise
        )

        text = offer.sdp.as_text()

        print("\n========== SDP OFFER ==========\n")
        print(text)
        print("========== END SDP OFFER ======\n")

        # 发送给浏览器
        self.send_signaling_message({
            "type": "offer",
            "sdp": text
        })

        print("[SIGNALING] SDP offer sent")

    # ============================================================
    # Remote SDP Answer
    # ============================================================

    def set_remote_answer(self, sdp_text):
        print("[WebRTC] Setting remote SDP answer")

        result, sdp_message = GstSdp.sdp_message_new_from_text(
            sdp_text
        )

        if result != GstSdp.SDPResult.OK:
            print("[WebRTC] Failed to parse SDP answer")
            return False

        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER,
            sdp_message
        )

        promise = Gst.Promise.new()

        self.webrtc.emit(
            "set-remote-description",
            answer,
            promise
        )

        print("[WebRTC] Remote SDP answer set")

        return False

    # ============================================================
    # ICE
    # ============================================================

    def on_ice_candidate(self, element, mlineindex, candidate):
        print("[WebRTC] ICE candidate:", candidate)

        self.send_signaling_message({
            "type": "candidate",
            "candidate": candidate,
            "sdpMLineIndex": mlineindex,
            "sdpMid": "video0"
        })

    def add_remote_candidate(
        self,
        candidate,
        sdp_mline_index,
        sdp_mid
    ):
        print("[WebRTC] Adding remote ICE candidate")

        try:
            self.webrtc.emit(
                "add-ice-candidate",
                sdp_mline_index,
                candidate
            )

            print("[WebRTC] Remote ICE candidate added")

        except Exception as e:
            print(
                "[WebRTC] Failed to add remote ICE candidate:",
                e
            )

        return False

    # ============================================================
    # GStreamer bus
    # ============================================================

    def on_bus_message(self, bus, message):
        msg_type = message.type

        if msg_type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()

            print("[GStreamer ERROR]")
            print(error)

            if debug:
                print(debug)

            self.loop.quit()

        elif msg_type == Gst.MessageType.WARNING:
            warning, debug = message.parse_warning()

            print("[GStreamer WARNING]")
            print(warning)

            if debug:
                print(debug)

        elif msg_type == Gst.MessageType.EOS:
            print("[GStreamer] EOS")
            self.loop.quit()

        elif msg_type == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending = (
                    message.parse_state_changed()
                )

                print(
                    f"[GStreamer] Pipeline: "
                    f"{old_state.value_nick} -> "
                    f"{new_state.value_nick}"
                )

        elif msg_type == Gst.MessageType.LATENCY:
            print("[GStreamer] LATENCY")

    # ============================================================
    # Start / Stop
    # ============================================================

    def start(self):
        print("[SIGNALING] Starting WebSocket...")

        self.start_websocket()

        print("[SIGNALING] Waiting for WebSocket connection...")

        if not self.ws_connected.wait(timeout=10):
            print("[SIGNALING] WebSocket connection timeout")
            sys.exit(1)

        print("[SIGNALING] WebSocket ready")

        print("[GStreamer] Starting pipeline...")

        ret = self.pipeline.set_state(
            Gst.State.PLAYING
        )

        if ret == Gst.StateChangeReturn.FAILURE:
            print("[GStreamer] Failed to start pipeline")
            sys.exit(1)

        self.loop.run()

    def stop(self):
        print("[GStreamer] Stopping pipeline...")

        self.pipeline.set_state(
            Gst.State.NULL
        )

        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    sender = WebRTCSender()

    try:
        sender.start()

    except KeyboardInterrupt:
        print("\nStopping...")

    finally:
        sender.stop()
