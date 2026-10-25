"""Fixture: launcher code outside the chart layout."""
import subprocess

subprocess.run(["helm", "install", "video-sender", "."], check=True)
