"""Prepare platform dependencies during application installation, never during SDK update."""
import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--wheelhouse', type=Path)
args = parser.parse_args()
destination = Path(__file__).resolve().parents[1] / 'src' / 'workbench' / 'bundled_wheels'
destination.mkdir(parents=True, exist_ok=True)
command = [sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--no-deps', '--dest', str(destination)]
if args.wheelhouse:
    command += ['--no-index', '--find-links', str(args.wheelhouse.resolve())]
command += ['pyserial>=3.5,<4', 'numpy>=1.24,<3']
subprocess.run(command, check=True)
