# -*- coding: utf-8 -*-
"""
Created on Fri May 22 10:02:56 2020

@author: Michael Schneider, Max Born Institut Berlin

Code bits to control LightField in an interactive session. This is just a
development helper file.
"""

# %% Imports

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
from tango import DevState, Attr, READ, READ_WRITE, DebugIt
from tango.server import Device, command, attribute

import time


# %% get the main application objects

lf = Automation(True, List[String]())
exp = lf.LightFieldApplication.Experiment
disp = lf.LightFieldApplication.DisplayManager.GetDisplay(0, 0)


# %% get current ROI object

roi = exp.SelectedRegions[0]

# try to get a list of attributes and methods
print(dir(roi))

# maybe iterable?
try:
    for n in roi:
        print(n)
except:
    pass

# %% or play around with a fresh ROI object:
new_roi = RegionOfInterest(0, 0, 1024, 1024, 1, 1)
print(dir(new_roi))


# %% get the image view data

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


livedata = disp.LiveDisplaySource.ImageDataSet
print('# frames:', livedata.Frames)
if livedata.Frames > 0:
    frame = livedata.GetFrame(0, 0)
    im_data = imageframe_to_numpy(frame)

# %% data ready event

def get_view_data(sender, event_args):
    global ev
    ev = event_args
    dataset = event_args.ImageDataSet
    print('# frames:', dataset.Frames)

exp.ImageDataSetReceived += get_view_data

exp.Acquire()
