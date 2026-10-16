import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from install import install_skill


class InstallTests(unittest.TestCase):
    def test_installs_portable_skill_and_preserves_existing_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, backup = install_skill(skills_dir=temporary)
            self.assertIsNone(backup)
            self.assertTrue((destination / "SKILL.md").is_file())
            client = destination / "scripts" / "chipseeker_remote.py"
            self.assertTrue(client.is_file())
            self.assertTrue((destination / "scripts" / "research_output.py").is_file())
            self.assertTrue((destination / "assets" / "report.css").is_file())
            self.assertTrue((destination / "assets" / "chipseeker_logo.jpg").is_file())
            self.assertTrue((destination / "references" / "search-strategy.md").is_file())
            result = subprocess.run([sys.executable, str(client), "--help"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("collect", result.stdout)
            marker = destination / "user-note.txt"
            marker.write_text("preserve", encoding="utf-8")
            reinstalled, backup = install_skill(skills_dir=temporary)
            self.assertEqual(reinstalled, destination)
            self.assertEqual((backup / "user-note.txt").read_text(encoding="utf-8"), "preserve")

    def test_installer_rejects_a_file_as_skill_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "chipseeker-ultra-search"
            destination.write_text("do not overwrite", encoding="utf-8")
            with self.assertRaises(ValueError):
                install_skill(skills_dir=temporary)
            self.assertEqual(destination.read_text(encoding="utf-8"), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
