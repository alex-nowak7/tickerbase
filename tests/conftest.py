import pathlib
import sys

# Let tests import the app's modules (audit_data, edgar_client, ...) from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
