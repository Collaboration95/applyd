"""The selected private root is never migrated or modified by selection."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workspace_config import select_root


class WorkspaceSelectionTests(unittest.TestCase):
    def test_explicit_overrides_environment_and_selection_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root = Path(a)
            existing = root / "private-fixture.json"
            original = b'{ "synthetic": true }\n'
            existing.write_bytes(original)
            with patch.dict(os.environ, {"APPLYD_WORKSPACE": b}):
                self.assertEqual(select_root(a), root.resolve())
                self.assertEqual(select_root(), Path(b).resolve())
            self.assertEqual(existing.read_bytes(), original)
