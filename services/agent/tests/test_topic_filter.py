import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from agent.main import is_actionable_topic


class TopicFilterTests(unittest.TestCase):
    def test_switch_state_topic_is_actionable(self):
        self.assertTrue(is_actionable_topic("home/ha/switch/demo/state"))

    def test_binary_sensor_state_topic_is_actionable(self):
        self.assertTrue(is_actionable_topic("home/ha/binary_sensor/demo/state"))

    def test_power_topic_is_actionable(self):
        self.assertTrue(is_actionable_topic("home/ha/sensor/demo_power"))

    def test_non_state_topic_is_not_actionable(self):
        self.assertFalse(is_actionable_topic("home/ha/switch/demo/availability"))


if __name__ == "__main__":
    unittest.main()
