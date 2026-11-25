"""Shared UTC clock without store import cycles."""
import time

def timestamp():
    return time.time()
