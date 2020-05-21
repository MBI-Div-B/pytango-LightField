# -*- coding: utf-8 -*-
"""
Created on Thu May 21 10:31:21 2020

@author: Michael Schneider, Max Born Institut Berlin
"""


import ctypes
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
from PrincetonInstruments.LightField.AddIns import ImageDataFormat

from System.Runtime.InteropServices import GCHandle, GCHandleType
from System import String
from System.Collections.Generic import List

import numpy as np

import tango
from tango import DevState, Attr, READ, READ_WRITE, DebugIt
from tango.server import Device, command, attribute


class LightFieldCamera(Device):
    ATTRS = [
        # camera settings
        dict(name='temp_read', label='sensor temperature', access=READ,
             dtype=tango.DevLong, unit='degC', lf=cs.SensorTemperatureReading),
        dict(name='temp_set', label='temperature setpoint', access=READ_WRITE,
              dtype=tango.DevLong, unit='degC', lf=cs.SensorTemperatureSetPoint),
        dict(name='temp_status', label='temperature locked', access=READ,
              dtype=tango.DevLong, lf=cs.SensorTemperatureStatus),
        dict(name='shutter_mode', label='shutter mode', access=READ_WRITE,
              dtype=tango.DevLong, lf=cs.ShutterTimingMode,
              description='1: normal, 2: open, 3: close'),
        dict(name='shutter_close', label='shutter closing time', access=READ_WRITE,
              dtype=tango.DevFloat, unit='ms', lf=cs.ShutterTimingClosingDelay),
        dict(name='exposure', label='exposure time', access=READ_WRITE,
              dtype=tango.DevLong, unit='ms', lf=cs.ShutterTimingExposureTime),
        dict(name='n_ports', label='readout ports', access=READ_WRITE,
              dtype=tango.DevLong, lf=cs.ReadoutControlPortsUsed),
        dict(name='adc_speed', label='ADC speed', access=READ_WRITE,
              dtype=tango.DevFloat, lf=cs.AdcSpeed, unit='MHz'),
        # experiment settings
        dict(name='n_frames', label='number of acquisitions', access=READ_WRITE,
              dtype=tango.DevLong, lf=es.AcquisitionFramesToStore),
        dict(name='save_folder', label='data folder', access=READ_WRITE,
              dtype=tango.DevString, lf=es.FileNameGenerationDirectory),
        dict(name='save_base', label='base name', access=READ_WRITE,
              dtype=tango.DevString, lf=es.FileNameGenerationBaseFileName),
        dict(name='save_index', label='file index', access=READ_WRITE,
              dtype=tango.DevLong, lf=es.FileNameGenerationIncrementNumber),
        dict(name='save_digits', label='index length', access=READ_WRITE,
              dtype=tango.DevLong, lf=es.FileNameGenerationIncrementMinimumDigits),
        dict(name='orient_on', label='apply image orientatiation',
              access=READ_WRITE, dtype=tango.DevBoolean,
              lf=es.OnlineCorrectionsOrientationCorrectionEnabled),
        dict(name='orient_hor', label='flip horizontally',
              access=READ_WRITE, dtype=tango.DevBoolean,
              lf=es.OnlineCorrectionsOrientationCorrectionFlipHorizontally),
        dict(name='orient_ver', label='flip vertically',
              access=READ_WRITE, dtype=tango.DevBoolean,
              lf=es.OnlineCorrectionsOrientationCorrectionFlipVertically),
        dict(name='orient_rot', label='rotate 90 degree',
              access=READ_WRITE, dtype=tango.DevLong,
              lf=es.OnlineCorrectionsOrientationCorrectionRotateClockwise),
        ]
    
    attr_keys = {d['name']: d['lf'] for d in ATTRS}
    
    image = attribute(name='image', label='CCD image', dtype=((float,),),
                      max_dim_x=2048, max_dim_y=2048, access=READ)
    
    def init_device(self):
        Device.init_device(self)
        # Create the LightField Application instance (true for visible)
        self.set_state(DevState.INIT)
        for d in self.ATTRS:
            self.make_attribute(d)
        self.lf = Automation(True, List[String]())
        print('lightfield started')
        self.exp = self.lf.LightFieldApplication.Experiment
        self.register_events()
        print('experiment loaded')
        self.disp = self.lf.LightFieldApplication.DisplayManager.GetDisplay(0, 0)
        self.setup_file_save()
        if self.check_camera_present():
            self.info_stream('Camera control started')
            self.set_state(DevState.ON)
        else:
            self.error_stream('No camera found.')
            self.set_state(DevState.FAULT)
    
    def setup_file_save(self):
        '''Make sure that file save options are correct.'''
        self.lightfield_set(es.FileNameGenerationAttachDate, False)
        self.lightfield_set(es.FileNameGenerationAttachTime, False)
        self.lightfield_set(es.FileNameGenerationAttachIncrement, True)
        return
    
    @DebugIt()
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
        new_attr = Attr(name, dtype, access)
        prop = tango.UserDefaultAttrProp()
        for k, v in attr_dict.items():
            try:
                setattr(prop, k, v)
            except AttributeError:
                pass
        
        new_attr.set_default_properties(prop)
        self.add_attribute(new_attr,
            r_meth=self.read_general,
            w_meth=self.write_general,
            )
        
    def check_camera_present(self):
        for device in self.exp.ExperimentDevices:
            if (device.Type == DeviceType.Camera):
                return True
        return False
    
    def lightfield_set(self, key, value):
        # self.debug_stream('in config_setter:')
        self.exp.SetValue(key, value)
    
    def lightfield_get(self, key):
        # self.debug_stream('in config getter:')
        # print('read', key)
        val = self.exp.GetValue(key)
        return val
    
    def read_general(self, attr):
        key = self.attr_keys[attr.get_name()]
        # self.debug_stream('reading', key)
        # print('read general:', key)
        attr.set_value(self.lightfield_get(key))
    
    def write_general(self, attr):
        key = self.attr_keys[attr.get_name()]
        val = attr.get_write_value()
        # print('write general', key, '->', val)
        # self.debug_stream('setting', key, '->', val)
        self.lightfield_set(key, val)
    
    def next_file_exists(self):
        '''Check whether the next file name is available.'''
        folder = self.lightfield_get(self.attr_keys['save_folder'])
        fname = self.lightfield_get(es.FileNameGenerationExampleFileName)
        fpath = os.path.join(folder, fname + '.spe')
        return os.path.exists(fpath)
    
    def read_image(self):
        return self.image.get_value()
    
    @command
    def acquire(self):
        while self.next_file_exists():
            index = self.lightfield_get(self.attr_keys['save_index'])
            print('file exists! Incrementing index.')
            self.lightfield_set(self.attr_keys['save_index'], index + 1)
        # self.set_state(DevState.MOVING)
        self.exp.Acquire()
    
    @command
    def stop(self):
        self.exp.Stop()
    
    @DebugIt()
    @command
    def preview(self):
        self.exp.Preview()
    
    @DebugIt()
    def handler_new_data(self, sender, event_args):
        print('data ready')
        frame = self.disp.LiveDisplaySource.ImageDataSet.GetFrame(0, 0)
        im_data = imageframe_to_numpy(frame)
        self.image.set_value(im_data)
        
    @DebugIt()
    def handler_acq_finished(self, sender, event_args):
        self.set_state(DevState.ON)
    
    @DebugIt()
    def handler_exp_ready(self, sender, event_args):
        print(event_args)
        if self.exp.IsReadyToRun:
            self.set_state(DevState.ON)
        else:
            self.set_state(DevState.RUNNING)
    
    @DebugIt()
    def handler_lightfield_close(self, sender, event_args):
        self.set_state(DevState.OFF)
    
    def register_events(self):
        self.exp.IsReadyToRunChanged += self.handler_exp_ready
        self.lf.LightFieldClosed += self.handler_lightfield_close
        self.exp.ImageDataSetReceived += self.handler_new_data
        

def imageframe_to_numpy(frame):
    '''
    Retrieve data from LightField DisplaySource.
    
    Parameters
    ----------
    frame : 
        LightField display source. Could be the live view or a loaded file.

    Returns
    -------
    data
        numpy array.
    '''
    buffer = frame.GetData()
    image_format = frame.Format
    src_hndl = GCHandle.Alloc(buffer, GCHandleType.Pinned)
    try:
        src_ptr = src_hndl.AddrOfPinnedObject().ToInt64()
        # Possible data types returned from acquisition
        if (image_format==ImageDataFormat.MonochromeUnsigned16):
            buf_type = ctypes.c_ushort*len(buffer)
        elif (image_format==ImageDataFormat.MonochromeUnsigned32):
            buf_type = ctypes.c_uint*len(buffer)
        elif (image_format==ImageDataFormat.MonochromeFloating32):
            buf_type = ctypes.c_float*len(buffer)
                    
        cbuf = buf_type.from_address(src_ptr)
        resultArray = np.frombuffer(cbuf, dtype=cbuf._type_)
    # Free the handle 
    finally:        
        if src_hndl.IsAllocated: src_hndl.Free()
    return np.copy(resultArray).reshape(frame.Width, frame.Height)


if __name__ == '__main__':
    LightFieldCamera.run_server()


