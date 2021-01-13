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
    # list of simple scalar controls that can be created automatically
    # required fields: name, access, dtype, lf
    # `lf` is the LightField settingName
    DYN_ATTRS = [
        # camera settings
        dict(name='temp_read', label='sensor temperature', access=READ,
             dtype=tango.DevFloat, unit='degC', lf=cs.SensorTemperatureReading),
        dict(name='temp_set', label='temperature setpoint', access=READ_WRITE,
              dtype=tango.DevFloat, unit='degC', lf=cs.SensorTemperatureSetPoint),
        # FIXME: this should be a DevEnum, which is currently bugged in
        # dynamic creation: https://github.com/tango-controls/pytango/pull/348
        dict(name='temp_status', label='temperature locked', access=READ,
              dtype=tango.DevLong, lf=cs.SensorTemperatureStatus,
              enum_labels=['invalid', 'unlocked', 'locked', 'fault']),
        # FIXME: DevEnum
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
        dict(name='accumulations', label='number of acquisitions per frame',
             access=READ_WRITE, dtype=tango.DevLong,
             lf=es.OnlineProcessingFrameCombinationFramesCombined),
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
                      max_dim_y=4096, dtype=((tango.DevFloat,),), access=READ)
    chip_shape = attribute(name='chip_shape', label='expected image shape',
                        access=READ, dtype=(int,), max_dim_x=4)
    
    def init_device(self):
        Device.init_device(self)
        self.set_change_event('image', True, False)
        self.set_state(DevState.INIT)
        self.lf = Automation(True, List[String]())  # starts LF instance
        self.exp = self.lf.LightFieldApplication.Experiment
        self.device = self.get_camera_device()
        if self.device is not None:
            self.set_state(DevState.ON)
            name, model, sn, shape = self.get_sensor_info()
            print('Connected:', model, name, sn, file=self.log_info)
            self._image = np.zeros(shape)
            self._chip_shape = shape
            self._sensorshape = shape
            self._accum = 0
            self.register_events()
            self.setup_file_save()
        else:
            print('No camera found.', file=self.log_error)
            self.set_state(DevState.FAULT)
    
    def initialize_dynamic_attributes(self):
        for d in self.DYN_ATTRS:
            self.make_attribute(d)
    
    def make_attribute(self, attr_dict):
        '''Dynamically generate simple attributes for LightField settings.'''
        baseprops = ['name', 'dtype', 'access', 'lf']
        name, dtype, access, lf = [attr_dict.pop(k) for k in baseprops]
        if self.exp.Exists(lf):
            print('making attribute', name, file=self.log_debug)
            new_attr = Attr(name, dtype, access)
            prop = tango.UserDefaultAttrProp()
            for k, v in attr_dict.items():
                try:
                    property_setter = getattr(prop, 'set_' + k)
                    property_setter(v)
                except AttributeError:
                    print("error setting attribute property:", name, k, v,
                          file=self.log_error)
            
            new_attr.set_default_properties(prop)
            self.add_attribute(
                new_attr,
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
    
    def get_sensor_info(self):
        '''Query the sensor name, model, serial number and active area.'''
        width = self.lightfield_get(cs.SensorInformationActiveAreaWidth)
        height = self.lightfield_get(cs.SensorInformationActiveAreaHeight)
        name = self.lightfield_get(cs.SensorInformationSensorName)
        model = self.device.Model
        serial = self.device.SerialNumber
        return name, model, serial, (height, width)
        
    def get_camera_device(self):
        '''Returns the first registered camera device.'''
        for device in self.exp.ExperimentDevices:
            if device.Type == DeviceType.Camera:
                return device
        return None
    
    def lightfield_set(self, key, value):
        if not self.exp.IsRunning:
            if self.exp.IsValid(key, value):
                self.exp.SetValue(key, value)
                print(f'set {key} -> {value}', file=self.log_debug)
            else:
                print(f'invalid setting: {key}->{value}', file=self.log_error)
        else:
            print(f'Cannot set {key}: acquiring', file=self.log_warn)
    
    def lightfield_get(self, key):
        val = self.exp.GetValue(key)
        return val
    
    def read_general(self, attr):
        key = self.attr_keys[attr.get_name()]
        print('reading', key, file=self.log_debug)
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
    
    def read_chip_shape(self):
        return self._chip_shape
    
    @command(dtype_in=int)
    def set_binning(self, N):
        '''Sets the camera to full chip binning mode.
        
        Use the `set_roi` command to setup a region of interest with binning.
        '''
        if not self.exp.IsRunning:
            if N > 1:
                self.exp.SetBinnedSensorRegion(N, N)
                self._imshape = [int(npx // N) for npx in self._sensorshape]
                print(f'full chip binning {N}x{N}', file=self.log_debug)
            else:
                self.exp.SetFullSensorRegion()
                self._imshape = self._sensorshape
                print('full chip unbinned', file=self.log_debug)
    
    @command(dtype_in=(int,), doc_in='list of ints [x0, x1, y0, y1, bin]',
             dtype_out=bool, doc_out='True if successful')
    def set_roi(self, roi):
        '''Sets the camera to a (possibly binned) ROI.
        
        input is a list of ints [x0, x1, y0, y1, binning]
        '''
        if not self.exp.IsRunning:
            if len(roi) == 4:
                x0, x1, y0, y1 = roi
                N = 1
            elif len(roi) > 4:
                x0, x1, y0, y1, N = roi
            else:
                print('cannot understand ROI', file=self.log_error)
                return False
            region = RegionOfInterest(x0, y0, x1 - x0, y1 - y0, N, N)
            self._imshape = [int((x1 - x0) // N), int((y1 - y0) // N)]
            
            self.exp.SetCustomRegions((region,))
            print('set custom ROI', file=self.log_debug)
            return True
        else:
            print('Cannot set ROI during acquisition', file=self.log_error)
            return False
    
    @command(dtype_out=(int,),
             doc_out='get width and height of the currently active ROI')
    def get_roi_size(self):
        '''Return image size for the current ROI settings.

        As some hardware supports separate non-contiguous regions in a single
        ROI, this always returns a spectrum of ints such as
        `[width0, height0, width1, height1, ...]`.'''
        rois = self.exp.SelectedRegions
        roi_size = []
        for roi in rois:
            roi_size += [roi.X, roi.Y, roi.Width, roi.Height, roi.XBinning, roi.YBinning]
        return roi_size

    @command
    def acquire(self):
        self.increment_to_next_free()
        if self.exp.IsReadyToRun:
            self._image = 0
            self._accum = 0
            self._preview = False
            self.exp.Acquire()
    
    @command
    def stop(self):
        self.exp.Stop()
    
    @command
    def preview(self):
        self.increment_to_next_free()
        if self.exp.IsReadyToRun:
            self._preview = True
            self.exp.Preview()
    
    def handler_new_data(self, sender, event_args):
        data = event_args.ImageDataSet
        if data.Frames > 0:
            frame = data.GetFrame(0, 0)
            im = imageframe_to_numpy(frame).astype(np.float32)
            if not self._preview:
                self._image = ((self._image * self._accum) + im) / (self._accum + 1)
                self._accum += 1
            else:
                self._image = im
            dim_x, dim_y = self._image.shape
            print('new image:', self._image.shape, file=self.log_info)
            self.push_change_event('image', self._image, dim_y, dim_x)
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
        image = np.frombuffer(cbuf, dtype=cbuf._type_).copy()
        image = np.rot90(image.reshape(frame.Height, frame.Width), -1).T
    finally:        
        if src_hndl.IsAllocated:
            src_hndl.Free()
    return image


if __name__ == '__main__':
    LightFieldCamera.run_server()


