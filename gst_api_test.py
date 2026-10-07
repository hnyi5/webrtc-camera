import gi

gi.require_version("Gst","1.0")
from gi.repository import Gst
from gi.overrides import Gst
Gst.Pipeline.__new__
Gst.init(None)


print("GStreamer version:",Gst.version())

pipeline = Gst.Pipeline.new("test_pipeline")

print("Pipeline:",pipeline)
factory = Gst.ElementFactory.find("v4l2src")
print("Factory:", factory)

gst-launch-1.0 videotestsrc ! "video/x-raw,width=1280,height=720" ! autovideosink