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
from PrincetonInstruments.LightField.AddIns import RegionOfInterest

from System.Runtime.InteropServices import GCHandle, GCHandleType
from System import String
from System.Collections.Generic import List

import numpy as np

import tango
from tango import DevState, Attr, READ, READ_WRITE
from tango.server import Device, command, attribute


class LightFieldCamera(Device):
    DYN_ATTRS = [
        # camera settings
        dict(name='temp_read', label='sensor temperature', access=READ,
             dtype=tango.DevFloat, unit='degC', lf=cs.SensorTemperatureReading),
        dict(name='temp_set', label='temperature setpoint', access=READ_WRITE,
              dtype=tango.DevFloat, unit='degC', lf=cs.SensorTemperatureSetPoint),
        dict(name='temp_status', label='temperature locked', access=READ,
              dtype=tango.DevLong, lf=cs.SensorTemperatureStatus,
              enum_labels=['invalid', 'unlocked', 'locked', 'fault']),
        dict(name='shutter_mode', label='shutter mode', access=READ_WRITE,
              dtype=tango.DevLong, lf=cs.ShutterTimingMode,
              enum_labels=['invalid', 'normal', 'closed', 'open', 'trigger']),
        dict(name='shutter_close', label='shutter closing time', access=READ_WRITE,
              dtype=tango.DevFloat, unit='ms', lf=cs.ShutterTimingClosingDelay),
        dict(name='exposure', label='exposure time', access=READ_WRITE,
              dtype=tango.DevFloat, unit='ms', lf=cs.ShutterTimingExposureTime),
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
              dtype=tango.DevLong, lf=es.FileNameGenerationIncrementNumber,
              min_value='0'),
        dict(name='save_digits', label='index length', access=READ_WRITE,
              dtype=tango.DevLong, min_value='1', max_value='10',
              lf=es.FileNameGenerationIncrementMinimumDigits),
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
    
    attr_keys = {d['name']: d['lf'] for d in DYN_ATTRS}
    
    image = attribute(name='image', label='CCD image', max_dim_x=4096,
                      max_dim_y=4096, dtype=((tango.DevUShort,),), access=READ)
    
    def init_device(self):
        Device.init_device(self)
        self._image = np.zeros((2048, 2048))
        self.set_change_event('image', True, False)
        # Create the LightField Application instance (true for visible)
        self.set_state(DevState.INIT)
        self.lf = Automation(True, List[String]())
        print('lightfield started')
        self.exp = self.lf.LightFieldApplication.Experiment
        self.register_events()
        print('experiment loaded')
        self.setup_file_save()
        if self.check_camera_present():
            self.set_state(DevState.ON)
        else:
            print('No camera found.', file=self.log_error)
            self.set_state(DevState.FAULT)
    
    def initialize_dynamic_attributes(self):
        for d in self.DYN_ATTRS:
            self.make_attribute(d)
    
    def make_attribute(self, attr_dict):
        '''Dynamically generate simple attributes for LightField settings.

        Parameters
        ----------
        attr_dict : dictionary

        Returns
        -------
        None.
        '''
        baseprops = ['name', 'dtype', 'access', 'lf']
        name, dtype, access, lf = [attr_dict.pop(k) for k in baseprops]
        if self.exp.Exists(lf):
            print('making attribute', name, file=self.log_debug)
            new_attr = Attr(name, dtype, access)
            prop = tango.UserDefaultAttrProp()
            for k, v in attr_dict.items():
                try:
                    meth = getattr(prop, 'set_' + k)
                    meth(v)
                except AttributeError:
                    print("error setting attribute property:", name, k, v,
                          file=self.log_error)
            
            new_attr.set_default_properties(prop)
            self.add_attribute(new_attr,
                r_meth=self.read_general,
                w_meth=self.write_general,
                )
        else:
            print(f'Skipping attribute {name}: Does not exist on this device',
                  file=self.log_warn)
    
    def setup_file_save(self):
        '''Make sure that file save options are correct.'''
        self.lightfield_set(es.FileNameGenerationAttachDate, False)
        self.lightfield_set(es.FileNameGenerationAttachTime, False)
        self.lightfield_set(es.FileNameGenerationAttachIncrement, True)
        return
    
    def check_camera_present(self):
        for device in self.exp.ExperimentDevices:
            if (device.Type == DeviceType.Camera):
                print(f'Connected: {device.Model}, S/N {device.SerialNumber}',
                      file=self.log_info)
                return True
        return False
    
    def lightfield_set(self, key, value):
        if not self.exp.IsRunning:
            if self.exp.IsValid(key, value):
                self.exp.SetValue(key, value)
                print(f'set {key} -> {value}', file=self.log_info)
            else:
                print(f'invalid setting: {key}->{value}', file=self.log_error)
        else:
            print(f'Cannot set {key}: acquiring', file=self.log_warn)
    
    def lightfield_get(self, key):
        val = self.exp.GetValue(key)
        return val
    
    def read_general(self, attr):
        key = self.attr_keys[attr.get_name()]
        # print('reading', str(key), file=self.log_debug)
        attr.set_value(self.lightfield_get(key))
    
    def write_general(self, attr):
        key = self.attr_keys[attr.get_name()]
        val = attr.get_write_value()
        print('setting', key, '->', val, file=self.log_debug)
        self.lightfield_set(key, val)
    
    def next_file_exists(self):
        '''Check whether the next file name is available.'''
        folder = self.lightfield_get(self.attr_keys['save_folder'])
        fname = self.lightfield_get(es.FileNameGenerationExampleFileName)
        fpath = os.path.join(folder, fname + '.spe')
        return os.path.exists(fpath)
    
    def increment_to_next_free(self):
        '''
        Make sure next file name is avilable by incrementing the file index.
        '''
        while self.next_file_exists():
            index = self.lightfield_get(self.attr_keys['save_index'])
            print('file exists! Incrementing index.', file=self.log_warn)
            self.lightfield_set(self.attr_keys['save_index'], index + 1)
    
    def read_image(self):
        return self._image
    
    @command(dtype_in=int)
    def set_binning(self, N):
        '''Sets the camera to full chip binning mode.
        
        Use the `set_roi` command to setup a region of interest with binning.
        '''
        if not self.exp.IsRunning:
            if N > 1:
                self.exp.SetBinnedSensorRegion(N, N)
                print(f'full chip binning {N}x{N}', file=self.log_debug)
            else:
                self.exp.SetFullSensorRegion()
                print('full chip unbinned', file=self.log_debug)
    
    @command(dtype_in=(int,), doc_in='list of ints [x0, x1, y0, y1, bin]',
             dtype_out=bool, doc_out='True if successful')
    def set_roi(self, roi):
        '''Sets the camera to a (possibly binned) ROI.
        
        input is a list of ints [x0, x1, y0, y1, binning]
        '''
        if not self.exp.IsRunning:
            if len(roi) == 4:
                x0, x1, y0, y1 = [roi[i] for i in range(4)]
                N = 1
            elif len(roi) > 4:
                x0, x1, y0, y1, N = [roi[i] for i in range(5)]
            else:
                print('cannot understand ROI', file=self.log_error)
                return False
            region = RegionOfInterest(x0, y0, x1 - x0, y1 - y0, N, N)
            
            self.exp.SetCustomRegions((region,))
            print('set custom ROI', file=self.log_debug)
            return True
        else:
            print('Cannot set ROI during acquisition', file=self.log_error)
            return False
    
    @command
    def acquire(self):
        self.increment_to_next_free()
        if self.exp.IsReadyToRun:
            self.exp.Acquire()
    
    @command
    def stop(self):
        self.exp.Stop()
    
    @command
    def preview(self):
        self.increment_to_next_free()
        if self.exp.IsReadyToRun:
            self.exp.Preview()
    
    def handler_new_data(self, sender, event_args):
        data = event_args.ImageDataSet
        if data.Frames > 0:
            frame = data.GetFrame(0, 0)
            self._image = imageframe_to_numpy(frame)
            
            dim_x, dim_y = self._image.shape
            print('new image:', self._image.shape, file=self.log_info)
            self.push_change_event('image', self._image, dim_x, dim_y)
        else:
            print('no frames:', data.Frames, file=self.log_error)
    
    def handler_acq_finished(self, sender, event_args):
        self.set_state(DevState.ON)
    
    def handler_acq_start(self, sender, event_args):
        self.set_state(DevState.RUNNING)
    
    def handler_lightfield_close(self, sender, event_args):
        self.set_state(DevState.OFF)
    
    def register_events(self):
        self.exp.ExperimentStarted += self.handler_acq_start
        self.exp.ExperimentCompleted += self.handler_acq_finished
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
        dtypes = {ImageDataFormat.MonochromeUnsigned16: ctypes.c_ushort,
                  ImageDataFormat.MonochromeUnsigned32: ctypes.c_uint,
                  ImageDataFormat.MonochromeFloating32: ctypes.c_float}
        buf_type = dtypes[image_format] * len(buffer)
        cbuf = buf_type.from_address(src_ptr)
        imagedata = np.frombuffer(cbuf, dtype=cbuf._type_)
    finally:        
        if src_hndl.IsAllocated:
            src_hndl.Free()
    return np.copy(imagedata).reshape(frame.Height, frame.Width)


if __name__ == '__main__':
    LightFieldCamera.run_server()


