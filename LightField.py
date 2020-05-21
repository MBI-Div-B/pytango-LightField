# -*- coding: utf-8 -*-
"""
Created on Thu May 21 10:31:21 2020

@author: Michael Schneider, Max Born Institut Berlin
"""


import sys
import os
import clr
sys.path.append(os.environ['LIGHTFIELD_ROOT'])
sys.path.append(os.environ['LIGHTFIELD_ROOT'] + '\\AddInViews')
clr.AddReference('PrincetonInstruments.LightFieldViewV5')
clr.AddReference('PrincetonInstruments.LightField.AutomationV5')
clr.AddReference('PrincetonInstruments.LightFieldAddInSupportServices')
clr.AddReference('System.Collections')
from PrincetonInstruments.LightField.Automation import Automation
from PrincetonInstruments.LightField.AddIns import CameraSettings as cs
from PrincetonInstruments.LightField.AddIns import ExperimentSettings as es
from PrincetonInstruments.LightField.AddIns import DeviceType

from System import String
from System.Collections.Generic import List

import tango
from tango import DevState, Attr, READ, READ_WRITE
from tango.server import Device, attribute, command



class LightFieldCamera(Device):
    ATTRS = [
        # camera settings
        dict(name='temp_read', label='sensor temperature', access=READ,
             dtype=tango.DevFloat, unit='°C', lf=cs.SensorTemperatureReading),
        dict(name='temp_set', label='temperature setpoint', access=READ_WRITE,
             dtype=tango.DevFloat, unit='°C', lf=cs.SensorTemperatureSetPoint),
        dict(name='temp_status', label='temperature locked', access=READ,
             dtype=tango.DevBoolean, lf=cs.SensorTemperatureStatus),
        dict(name='shutter_open', label='shutter opening time', access=READ_WRITE,
             dtype=tango.DevFloat, unit='ms', lf=cs.ShutterTimingOpeningDelay),
        dict(name='shutter_close', label='shutter closing time', access=READ_WRITE,
             dtype=tango.DevFloat, unit='ms', lf=cs.ShutterTimingClosingDelay),
        dict(name='exposure', label='exposure time', access=READ_WRITE,
             dtype=tango.DevFloat, unit='ms', lf=cs.ShutterTimingExposureTime),
        # dict(name='n_ports', label='readout ports', access=READ_WRITE,
             # dtype=tango.DevInt, lf=cs.ReadoutControlPortsUsed),
        # dict(name='adc_speed', label='ADC speed', access=READ_WRITE,
        #      dtype=tango.DevEnum, lf=cs.AdcSpeed, unit='MHz',
        #      enum_labels=[1.0, 0.5, 0.1]),
        # experiment settings
        dict(name='n_frames', label='number of acquisitions', access=READ_WRITE,
             dtype=tango.DevFloat, unit='ms', lf=es.AcquisitionFramesToStore),
        dict(name='save_folder', label='data folder', access=READ_WRITE,
             dtype=tango.DevString, lf=es.FileNameGenerationDirectory),
        dict(name='save_base', label='base name', access=READ_WRITE,
             dtype=tango.DevString, lf=es.FileNameGenerationBaseFileName),
        dict(name='save_index', label='file index', access=READ_WRITE,
             dtype=tango.DevString, lf=es.FileNameGenerationIncrementNumber),
        dict(name='save_digits', label='index length', access=READ_WRITE,
             dtype=tango.DevString, lf=es.FileNameGenerationIncrementMinimumDigits),
        dict(name='orient_on', label='apply image orientatiation',
             access=READ_WRITE, dtype=tango.DevBoolean,
             lf=es.OnlineCorrectionsOrientationCorrectionEnabled),
        dict(name='orient_hor', label='flip horizontally',
             access=READ_WRITE, dtype=tango.DevBoolean,
             lf=es.OnlineCorrectionsOrientationCorrectionFlipHorizontally),
        dict(name='orient_ver', label='flip vertically',
             access=READ_WRITE, dtype=tango.DevBoolean,
             lf=es.OnlineCorrectionsOrientationCorrectionFlipVertically),
        dict(name='orient_rot', label='rotate 90°',
             access=READ_WRITE, dtype=tango.DevBoolean,
             lf=es.OnlineCorrectionsOrientationCorrectionRotateClockwise),
        ]
    
    def init_device(self):
        Device.init_device(self)
        # Create the LightField Application instance (true for visible)
        self.set_state(DevState.INIT)
        for d in self.ATTRS:
            self.make_attribute(d)
        self.lf = Automation(True, List[String]())
        print('lightfield started')
        self.exp = self.lf.LightFieldApplication.Experiment
        print('experiment loaded')
        self.setup_file_save()
        if self.check_camera_present():
            self.info_stream('Camera control started')
            self.set_state(DevState.ON)
        else:
            self.error_stream('No camera found.')
            self.set_state(DevState.FAULT)
    
    def setup_file_save(self):
        '''Make sure that file save options are correct.'''
        self.lf_setter(es.FileNameGenerationAttachDate, False)
        self.lf_setter(es.FileNameGenerationAttachTime, False)
        self.lf_setter(es.FileNameGenerationAttachIncrement, True)
        return
    
    def make_attribute(self, attr_dict):
        '''Dynamically generate simple attributes for LightField settings.

        Parameters
        ----------
        attr_dict : dictionary

        Returns
        -------
        None.
        '''
        self.debug_stream('in make attribute')
        name, dtype, access, lf = [attr_dict.pop(k) for k in ['name', 'dtype', 'access', 'lf']]
        new_attr = self.add_attribute(
            Attr(name, dtype, access),
            r_meth=lambda: self.lf_getter(lf),
            w_meth=lambda v: self.lf_setter(lf, v),
            )
        
        prop = tango.UserDefaultAttrProp()
        if 'label' in attr_dict:
            prop.set_label(attr_dict['label'])
        if 'unit' in attr_dict:
            prop.set_unit(attr_dict['unit'])
        if 'enum_labels' in attr_dict:
            prop.set_enum_labels(attr_dict['enum_labels'])
        
        new_attr.set_default_properties(prop)
        return
        
    def check_camera_present(self):
        for device in self.exp.ExperimentDevices:
            if (device.Type == DeviceType.Camera):
                return True
        return False
    
    def lf_setter(self, key, value):
        self.debug_stream('in config_setter:')
        self.exp.SetValue(key, value)
    
    def lf_getter(self, key):
        self.debug_stream('in config getter:')
        self.exp.GetValue(key)
    

if __name__ == '__main__':
    LightFieldCamera.run_server()


        