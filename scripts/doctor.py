import platform
import sys

print("python", sys.version.split()[0], platform.machine())

try:
    import torch

    print("MPS available:", torch.backends.mps.is_available())
except ImportError:
    print("Camera dependencies aren't synced (run: make sync-cctv)")
