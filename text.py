import time

import gi

gi.require_version("Gst","1.0")
from gi.repository import Gst



Gst.init(None)


wall_ns = time.time_ns()
print(wall_ns)