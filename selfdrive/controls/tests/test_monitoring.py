import unittest
import numpy as np
from cereal import car
from common.realtime import DT_DMON
from selfdrive.controls.lib.events import Events
from selfdrive.controls.lib.driver_monitor import DriverStatus, MAX_TERMINAL_ALERTS, \
                                  _AWARENESS_TIME, _AWARENESS_PRE_TIME_TILL_TERMINAL, \
                                  _AWARENESS_PROMPT_TIME_TILL_TERMINAL, _DISTRACTED_TIME, \
                                  _DISTRACTED_PRE_TIME_TILL_TERMINAL, _DISTRACTED_PROMPT_TIME_TILL_TERMINAL
from selfdrive.controls.lib.gps_helpers import is_rhd_region

EventName = car.CarEvent.EventName

_TEST_TIMESPAN = 120 # seconds
_DISTRACTED_SECONDS_TO_ORANGE = _DISTRACTED_TIME - _DISTRACTED_PROMPT_TIME_TILL_TERMINAL + 1
_DISTRACTED_SECONDS_TO_RED = _DISTRACTED_TIME + 1
_INVISIBLE_SECONDS_TO_ORANGE = _AWARENESS_TIME - _AWARENESS_PROMPT_TIME_TILL_TERMINAL + 1
_INVISIBLE_SECONDS_TO_RED = _AWARENESS_TIME + 1

class fake_DM_msg():
  def __init__(self, is_face_detected, is_distracted=False):
    self.faceOrientation = [0.,0.,0.]
    self.facePosition = [0.,0.]
    self.faceProb = 1. * is_face_detected
    self.leftEyeProb = 1.
    self.rightEyeProb = 1.
    self.leftBlinkProb = 1. * is_distracted
    self.rightBlinkProb = 1. * is_distracted


# driver state from neural net, 10Hz
msg_NO_FACE_DETECTED = fake_DM_msg(is_face_detected=False)
msg_ATTENTIVE = fake_DM_msg(is_face_detected=True)
msg_DISTRACTED = fake_DM_msg(is_face_detected=True, is_distracted=True)

# driver interaction with car
car_interaction_DETECTED = True
car_interaction_NOT_DETECTED = False

# openpilot state
openpilot_ENGAGED = True
openpilot_NOT_ENGAGED = False

# car standstill state
car_STANDSTILL = True
car_NOT_STANDSTILL = False

# some common state vectors
always_no_face = [msg_NO_FACE_DETECTED] * int(_TEST_TIMESPAN/DT_DMON)
always_attentive = [msg_ATTENTIVE] * int(_TEST_TIMESPAN/DT_DMON)
always_distracted = [msg_DISTRACTED] * int(_TEST_TIMESPAN/DT_DMON)
always_true = [True] * int(_TEST_TIMESPAN/DT_DMON)
always_false = [False] * int(_TEST_TIMESPAN/DT_DMON)

def run_DState_seq(driver_state_msgs, driver_car_interaction, openpilot_status, car_standstill_status):
  # inputs are all 10Hz
  DS = DriverStatus()
  events_from_DM = []
  for idx in range(len(driver_state_msgs)):
    e = Events()
    DS.get_pose(driver_state_msgs[idx], [0, 0, 0], 0, openpilot_status[idx])
    # cal_rpy and car_speed don't matter here

    # evaluate events at 10Hz for tests
    DS.update(e, driver_car_interaction[idx], openpilot_status[idx], car_standstill_status[idx])
    events_from_DM.append(e)
  assert len(events_from_DM) == len(driver_state_msgs), 'somethings wrong'
  return events_from_DM, DS

class TestMonitoring(unittest.TestCase):
  # 0. op engaged, driver is doing fine all the time
  def test_fully_aware_driver(self):
    events_output = run_DState_seq(always_attentive, always_false, always_true, always_false)[0]
    self.assertTrue(np.sum([len(event) for event in events_output]) == 0)

  # 1. op engaged, driver is distracted and does nothing
  def test_fully_distracted_driver(self):
    events_output, d_status = run_DState_seq(always_distracted, always_false, always_true, always_false)
    self.assertTrue(len(events_output[int((_DISTRACTED_TIME-_DISTRACTED_PRE_TIME_TILL_TERMINAL)/2/DT_DMON)]) == 0)
    self.assertEqual(events_output[int((_DISTRACTED_TIME-_DISTRACTED_PRE_TIME_TILL_TERMINAL +\
                      ((_DISTRACTED_PRE_TIME_TILL_TERMINAL-_DISTRACTED_PROMPT_TIME_TILL_TERMINAL)/2))/DT_DMON)].names[0], EventName.preDriverDistracted)
    self.assertEqual(events_output[int((_DISTRACTED_TIME-_DISTRACTED_PROMPT_TIME_TILL_TERMINAL +\
                      ((_DISTRACTED_PROMPT_TIME_TILL_TERMINAL)/2))/DT_DMON)].names[0], EventName.promptDriverDistracted)
    self.assertEqual(events_output[int((_DISTRACTED_TIME +\
                      ((_TEST_TIMESPAN-10-_DISTRACTED_TIME)/2))/DT_DMON)].names[0], EventName.driverDistracted)
    self.assertIs(type(d_status.awareness), float)

  # 2. op engaged, no face detected the whole time, no action
  def test_fully_invisible_driver(self):
    events_output = run_DState_seq(always_no_face, always_false, always_true, always_false)[0]
    self.assertTrue(len(events_output[int((_AWARENESS_TIME-_AWARENESS_PRE_TIME_TILL_TERMINAL)/2/DT_DMON)]) == 0)
    self.assertEqual(events_output[int((_AWARENESS_TIME-_AWARENESS_PRE_TIME_TILL_TERMINAL +\
                      ((_AWARENESS_PRE_TIME_TILL_TERMINAL-_AWARENESS_PROMPT_TIME_TILL_TERMINAL)/2))/DT_DMON)].names[0], EventName.preDriverUnresponsive)
    self.assertEqual(events_output[int((_AWARENESS_TIME-_AWARENESS_PROMPT_TIME_TILL_TERMINAL +\
                      ((_AWARENESS_PROMPT_TIME_TILL_TERMINAL)/2))/DT_DMON)].names[0], EventName.promptDriverUnresponsive)
    self.assertEqual(events_output[int((_AWARENESS_TIME +\
                      ((_TEST_TIMESPAN-10-_AWARENESS_TIME)/2))/DT_DMON)].names[0], EventName.driverUnresponsive)

  # 3. op engaged, down to orange, driver pays attention, back to normal; then down to orange, driver touches wheel
  #  - should have short orange recovery time and no green afterwards; should recover rightaway on wheel touch
  def test_normal_driver(self):
    ds_vector = [msg_DISTRACTED] * int(_DISTRACTED_SECONDS_TO_ORANGE/DT_DMON) + \
                [msg_ATTENTIVE] * int(_DISTRACTED_SECONDS_TO_ORANGE/DT_DMON) + \
                [msg_DISTRACTED] * (int(_TEST_TIMESPAN/DT_DMON)-int(_DISTRACTED_SECONDS_TO_ORANGE*2/DT_DMON))
    interaction_vector = [car_interaction_NOT_DETECTED] * int(_DISTRACTED_SECONDS_TO_ORANGE*3/DT_DMON) + \
                        [car_interaction_DETECTED] * (int(_TEST_TIMESPAN/DT_DMON)-int(_DISTRACTED_SECONDS_TO_ORANGE*3/DT_DMON))
    events_output = run_DState_seq(ds_vector, interaction_vector, always_true, always_false)[0]
    self.assertTrue(len(events_output[int(_DISTRACTED_SECONDS_TO_ORANGE*0.5/DT_DMON)]) == 0)
    self.assertEqual(events_output[int((_DISTRACTED_SECONDS_TO_ORANGE-0.1)/DT_DMON)].names[0], EventName.promptDriverDistracted)
    self.assertTrue(len(events_output[int(_DISTRACTED_SECONDS_TO_ORANGE*1.5/DT_DMON)]) == 0)
    self.assertEqual(events_output[int((_DISTRACTED_SECONDS_TO_ORANGE*3-0.1)/DT_DMON)].names[0], EventName.promptDriverDistracted)
    self.assertTrue(len(events_output[int((_DISTRACTED_SECONDS_TO_ORANGE*3+0.1)/DT_DMON)]) == 0)

  # 4. op engaged, down to orange, driver dodges camera, then comes back still distracted, down to red, \
  #                          driver dodges, and then touches wheel to no avail, disengages and reengages
  #  - orange/red alert should remain after disappearance, and only disengaging clears red
  def test_biggest_comma_fan(self):
    _invisible_time = 2 # seconds
    ds_vector = always_distracted[:]
    interaction_vector = always_false[:]
    op_vector = always_true[:]
    ds_vector[int(_DISTRACTED_SECONDS_TO_ORANGE/DT_DMON):int((_DISTRACTED_SECONDS_TO_ORANGE+_invisible_time)/DT_DMON)] = [msg_NO_FACE_DETECTED] * int(_invisible_time/DT_DMON)
    ds_vector[int((_DISTRACTED_SECONDS_TO_RED+_invisible_time)/DT_DMON):int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time)/DT_DMON)] = [msg_NO_FACE_DETECTED] * int(_invisible_time/DT_DMON)
    interaction_vector[int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time+0.5)/DT_DMON):int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time+1.5)/DT_DMON)] = [True] * int(1/DT_DMON)
    op_vector[int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time+2.5)/DT_DMON):int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time+3)/DT_DMON)] = [False] * int(0.5/DT_DMON)
    events_output = run_DState_seq(ds_vector, interaction_vector, op_vector, always_false)[0]
    self.assertEqual(events_output[int((_DISTRACTED_SECONDS_TO_ORANGE+0.5*_invisible_time)/DT_DMON)].names[0], EventName.promptDriverDistracted)
    self.assertEqual(events_output[int((_DISTRACTED_SECONDS_TO_RED+1.5*_invisible_time)/DT_DMON)].names[0], EventName.driverDistracted)
    self.assertEqual(events_output[int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time+1.5)/DT_DMON)].names[0], EventName.driverDistracted)
    self.assertTrue(len(events_output[int((_DISTRACTED_SECONDS_TO_RED+2*_invisible_time+3.5)/DT_DMON)]) == 0)

  # 5. op engaged, invisible driver, down to orange, driver touches wheel; then down to orange again, driver appears
  #  - both actions should clear the alert, but momentary appearence should not
  def test_sometimes_transparent_commuter(self):
      _visible_time = np.random.choice([1, 10]) # seconds
      # print _visible_time
      ds_vector = always_no_face[:]*2
      interaction_vector = always_false[:]*2
      ds_vector[int((2*_INVISIBLE_SECONDS_TO_ORANGE+1)/DT_DMON):int((2*_INVISIBLE_SECONDS_TO_ORANGE+1+_visible_time)/DT_DMON)] = [msg_ATTENTIVE] * int(_visible_time/DT_DMON)
      interaction_vector[int((_INVISIBLE_SECONDS_TO_ORANGE)/DT_DMON):int((_INVISIBLE_SECONDS_TO_ORANGE+1)/DT_DMON)] = [True] * int(1/DT_DMON)
      events_output = run_DState_seq(ds_vector, interaction_vector, 2*always_true, 2*always_false)[0]
      self.assertTrue(len(events_output[int(_INVISIBLE_SECONDS_TO_ORANGE*0.5/DT_DMON)]) == 0)
      self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE-0.1)/DT_DMON)].names[0], EventName.promptDriverUnresponsive)
      self.assertTrue(len(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE+0.1)/DT_DMON)]) == 0)
      if _visible_time == 1:
        self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE*2+1-0.1)/DT_DMON)].names[0], EventName.promptDriverUnresponsive)
        self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE*2+1+0.1+_visible_time)/DT_DMON)].names[0], EventName.preDriverUnresponsive)
      elif _visible_time == 10:
        self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE*2+1-0.1)/DT_DMON)].names[0], EventName.promptDriverUnresponsive)
        self.assertTrue(len(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE*2+1+0.1+_visible_time)/DT_DMON)]) == 0)
      else:
        pass

  # 6. op engaged, invisible driver, down to red, driver appears and then touches wheel, then disengages/reengages
  #  - only disengage will clear the alert
  def test_last_second_responder(self):
    _visible_time = 2 # seconds
    ds_vector = always_no_face[:]
    interaction_vector = always_false[:]
    op_vector = always_true[:]
    ds_vector[int(_INVISIBLE_SECONDS_TO_RED/DT_DMON):int((_INVISIBLE_SECONDS_TO_RED+_visible_time)/DT_DMON)] = [msg_ATTENTIVE] * int(_visible_time/DT_DMON)
    interaction_vector[int((_INVISIBLE_SECONDS_TO_RED+_visible_time)/DT_DMON):int((_INVISIBLE_SECONDS_TO_RED+_visible_time+1)/DT_DMON)] = [True] * int(1/DT_DMON)
    op_vector[int((_INVISIBLE_SECONDS_TO_RED+_visible_time+1)/DT_DMON):int((_INVISIBLE_SECONDS_TO_RED+_visible_time+0.5)/DT_DMON)] = [False] * int(0.5/DT_DMON)
    events_output = run_DState_seq(ds_vector, interaction_vector, op_vector, always_false)[0]
    self.assertTrue(len(events_output[int(_INVISIBLE_SECONDS_TO_ORANGE*0.5/DT_DMON)]) == 0)
    self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_ORANGE-0.1)/DT_DMON)].names[0], EventName.promptDriverUnresponsive)
    self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_RED-0.1)/DT_DMON)].names[0], EventName.driverUnresponsive)
    self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_RED+0.5*_visible_time)/DT_DMON)].names[0], EventName.driverUnresponsive)
    self.assertEqual(events_output[int((_INVISIBLE_SECONDS_TO_RED+_visible_time+0.5)/DT_DMON)].names[0], EventName.driverUnresponsive)
    self.assertTrue(len(events_output[int((_INVISIBLE_SECONDS_TO_RED+_visible_time+1+0.1)/DT_DMON)]) == 0)

  # 7. op not engaged, always distracted driver
  #  - dm should stay quiet when not engaged
  def test_pure_dashcam_user(self):
    events_output = run_DState_seq(always_distracted, always_false, always_false, always_false)[0]
    self.assertTrue(np.sum([len(event) for event in events_output]) == 0)

  # 8. op engaged, car stops at traffic light, down to orange, no action, then car starts moving
  #  - should only reach green when stopped, but continues counting down on launch
  def test_long_traffic_light_victim(self):
    _redlight_time = 60 # seconds
    standstill_vector = always_true[:]
    standstill_vector[int(_redlight_time/DT_DMON):] = [False] * int((_TEST_TIMESPAN-_redlight_time)/DT_DMON)
    events_output = run_DState_seq(always_distracted, always_false, always_true, standstill_vector)[0]
    self.assertEqual(events_output[int((_DISTRACTED_TIME-_DISTRACTED_PRE_TIME_TILL_TERMINAL+1)/DT_DMON)].names[0], EventName.preDriverDistracted)
    self.assertEqual(events_output[int((_redlight_time-0.1)/DT_DMON)].names[0], EventName.preDriverDistracted)
    self.assertEqual(events_output[int((_redlight_time+0.5)/DT_DMON)].names[0], EventName.promptDriverDistracted)

if __name__ == "__main__":
  print('MAX_TERMINAL_ALERTS', MAX_TERMINAL_ALERTS)
  unittest.main()
