"""Render a 36-frame turntable of the open assembly into ~/Documents/Halo print files/turntable.
Run in Fusion's Text Commands (Py). scripts/make_turntable.py turns the frames into the README gif."""
import math
import os

import adsk.core

app = adsk.core.Application.get()
v = app.activeViewport
out = os.path.join(os.path.expanduser("~"), "Documents", "Halo print files", "turntable")
os.makedirs(out, exist_ok=True)
T = adsk.core.Point3D.create(0, 13, 28)       # cm, middle of the machine
R, H, N = 230, 95, 36
for i in range(N):
    a = math.radians(-90 + i * 360 / N)
    c = v.camera
    c.cameraType = adsk.core.CameraTypes.PerspectiveCameraType
    c.target = T
    c.eye = adsk.core.Point3D.create(T.x + R * math.cos(a), T.y + R * math.sin(a), T.z + H)
    c.upVector = adsk.core.Vector3D.create(0, 0, 1)
    c.isFitView = False
    c.isSmoothTransition = False
    v.camera = c
    v.refresh()
    adsk.doEvents()
    v.saveAsImageFile(os.path.join(out, "f%02d.png" % i), 640, 640)
print("turntable: %d frames in %s" % (N, out))
