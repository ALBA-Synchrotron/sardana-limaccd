###############################################################################
#
# This file is part of Sardana
#
# http://www.tango-controls.org/static/sardana/latest/doc/html/index.html
#
# Copyright 2019 CELLS / ALBA Synchrotron, Bellaterra, Spain
#
# Sardana is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Sardana is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Sardana.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################

import re
import os
import time
import struct
import logging

import numpy
import tango

from sardana import State
from sardana.pool.controller import TwoDController, OneDController, \
    Type, Description, Access, DataAccess, Memorize, NotMemorized, Memorized, \
    DefaultValue, MaxDimSize, AcqSynch, Referable


__author__ = 'Roberto Javier Homs Puron'
__copyright__ = 'Copyright 2019 CELLS / ALBA Synchrotron, Bellaterra, Spain'
__docformat__ = "restructuredtext"

__all__ = ['LimaCCDTwoDController', 'LimaCCDOneDController']


LIMA_ATTRS = {'cameramodel': 'camera_model',
              'cameratype': 'camera_type',
              'savingcommonheader': 'saving_common_header',
              'savingframeperfile': 'saving_frame_per_file',
              'savingheaderdelimiter': 'saving_header_delimiter',
              'savingmanagemode': 'saving_manage_mode',
              'savingmaxwritingtask': 'saving_max_writing_task',
              'savingmode': 'saving_mode',
              'savingnextnumber': 'saving_next_number',
              'savingoverwritepolicy': 'saving_overwrite_policy',
              'savingprefix': 'saving_prefix',
              'savingsuffix': 'saving_suffix'}

# TODO: Include the other formats
LIMA_EXT_FORMAT = {'EDF': ['.edf'],
                   'HDF5': ['.h5', '.hdf5'],
                   'CBF': ['.cbf'],
                   'TIFF': ['.tiff']}


CTRL_PROPERTIES = {
    'LimaCCDDeviceName': {Type: str, Description: 'Detector device name'},
    'LatencyTime': {Type: float, Description: 'Maximum latency time'},
    'FirstImageNumber': {Type: int,
                         Description: 'First value of the saving next number'}
}

CTRL_ATTRIBUTES = {
    'CameraModel': {
        Type: str,
        Description: 'LimaCCD attribute camera_model',
        Access: DataAccess.ReadOnly,
        Memorize: NotMemorized},
    'CameraType': {
        Type: str,
        Description: 'LimaCCD attribute camera_type',
        Access: DataAccess.ReadOnly,
        Memorize: NotMemorized},
    'SavingCommonHeader': {
        Type: [str, ],
        Description: 'LimaCCD attribute saving_common_header',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized},
    'SavingFramePerFile': {
        Type: int,
        Description: 'LimaCCD attribute saving_frame_per_file',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized,
        DefaultValue: 1},
    'SavingHeaderDelimiter': {
        Type: [str, ],
        Description: 'LimaCCD attribute saving_header_delimiter',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized},
    'SavingManagedMode': {
        Type: str,
        Description: 'LimaCCD attribute saving_managed_mode',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized},
    'SavingMaxWritingTask': {
        Type: int,
        Description: 'LimaCCD attribute saving_max_writing_task',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized},
    'SavingMode': {
        Type: str,
        Description: 'LimaCCD attribute saving_mode. The controller will'
                     'set it to AUTO_FRAME by default.',
        Access: DataAccess.ReadWrite,
        Memorize: NotMemorized,
        DefaultValue: 'AUTO_FRAME'},
    'SavingNextNumber': {
        Type: int,
        Description: 'LimaCCD attribute saving_next_number',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized},
    'SavingOverwritePolicy': {
        Type: str,
        Description: 'LimaCCD attribute saving_overwrite_policy',
        Access: DataAccess.ReadWrite,
        Memorize: Memorized,
        DefaultValue: 'ABORT'},
    'SavingFormatsAllowed': {
        Type:  [str, ],
        Description: 'Detector SavingModes allowed saving_suffix',
        Access: DataAccess.ReadOnly,
        Memorize: NotMemorized},
    'SavingImageHeaders': {
        Type: [str, ],
        Description: 'Headers for each image',
        Access: DataAccess.ReadWrite,
        Memorize: NotMemorized,
        MaxDimSize: (1000000,)},
    }


class LimaImageFormat(struct.Struct):

    Magic = 0x44544159
    DTypes = ('u1', 'u2', 'u4', None, 'i1', 'i2', 'i4')
    DTypeSize = (1, 2, 4, None, 1, 2, 4)

    def __init__(self):
        super().__init__('<IHHIIHHHHHHHHIIIIIIII')


    def decode(self, buff, n=1):
        header = self.unpack_from(buff)
        magic, version, hsize, cat, typ, big_endian, ndim, d1, d2 = header[:9]
        assert magic == self.Magic
        assert version == 2
        assert hsize == self.size
        dtype = self.DTypes[typ]
        pixel_size = self.DTypeSize[typ]
        nb_pixels = d1 * d2
        frame_size = nb_pixels * pixel_size
        frame_data_size = hsize + frame_size
        # Reasonable assumption: all frames have the same format
        frames = []
        for i in range(n):
            offset = i * frame_data_size + hsize
            frame = numpy.frombuffer(
                buff, count=nb_pixels, dtype=dtype, offset=offset
            )
            frame.shape = d1, d2
            frames.append(frame)
        return frames


LIMA_DECODER = LimaImageFormat()


def read_lima_frames(dev, frame_start, frame_end):
    fmt, buff = dev.command_inout('readImageSeq', (frame_start, frame_end))
    assert fmt == 'DATA_ARRAY'
    return LIMA_DECODER.decode(buff, n=frame_end - frame_start)


class Lima:
    """LimaCCD Controller helper class"""

    def __init__(self, device_name, latency_time, first_image_nb=0, log=None):
        self._log = log if log else logging.getLogger("Lima")
        try:
            self._limaccd = tango.DeviceProxy(device_name)
            self._limaccd.reset()
        except tango.DevFailed as e:
            raise RuntimeError('__init__(): Could not create a device proxy '
                               'from following device name: %s.\nException: '
                               '%r ' % (device_name, e))

        self._latency_time = latency_time
        self._first_image_nb = first_image_nb
        self._synchronization = AcqSynch.SoftwareTrigger
        self._value_ref_pattern = ''
        self._value_ref_enabled = False
        self._skipp_load = False
        self._skipp_start = False
        self._last_image_read = -1
        self._image_next_number = 0
        self._new_data = False
        self._aborted_flg = False
        self._started_flg = False
        self._first_start = False
        self._image_pattern = ''
        self._nb_frames = 0
        self.return_seq = False

        # Get the Detector Saving Modes allowed
        formats = self._limaccd.command_inout('getAttrStringValueList',
                                              'saving_format')
        self._saving_formats_allowed = formats

    def _clean_variables(self):
        self._skipp_load = False
        self._skipp_start = False
        self._new_data = False
        self._aborted_flg = False
        self._started_flg = False
        self._first_start = False
        self._last_image_read = -1
        self._image_next_number = 0
        self._nb_frames = 0
        self.return_seq = False

    def _prepareAcq(self):
        self._limaccd.prepareAcq()
        while True:
            attr = 'last_image_saved'
            new_image_ready = self._limaccd.read_attribute(attr).value
            if new_image_ready == -1:
                break
            time.sleep(0.01)

    def AddDevice(self, axis):
        if axis != 1:
            raise ValueError('This controller only have the axis 1')

    READY = State.On, 'The LimaCCD is ready to acquire'
    MOVING = State.Moving, 'The LimaCCD is acquiring'

    def StateOne(self, axis):
        attrs = 'acq_status', 'last_image_saved', 'last_image_ready'
        acq_status, saved, ready = [
            a.value
            for a in self._limaccd.read_attributes(attrs)
        ]
        self._log.debug(
            "status=%s | acquired=%d saved=%d", acq_status, ready, saved
        )
        if acq_status not in {'Ready', 'Running'}:
            result = State.Fault, 'The LimaCCD state is: {}'.format(acq_status)
        else:
            if self._value_ref_enabled:
                images_to_save = False
                if saved == -1 or saved < self._nb_frames - 1:
                    images_to_save = True

                if (acq_status == 'Running' or images_to_save) and not self._aborted_flg:
                    result = self.MOVING
                else:
                    result = self.READY
            else:
                if acq_status == 'Running' or ready < self._nb_frames - 1:
                    result = self.MOVING
                else:
                    result = self.READY

        self._log.debug('Status: %s', result[1])
        return result

    def PrepareOne(self, axis, value, repetitions, latency, nb_starts):
        self._log.debug("PrepareOne v=%s rep=%s lat=%s nb_starts=%s",
            value, repetitions, latency, nb_starts)
        self._clean_variables()

        acq_nb_frames = repetitions * nb_starts
        self._nb_frames = acq_nb_frames
        acq_expo_time = value
        latency_time = latency
        # Configure saving
        if self._value_ref_enabled:
            self._limaccd.write_attribute('saving_mode', 'AUTO_FRAME')

            # TODO: Improve regexp matching
            # Extract saving configuration from the pattern
            try:
                dir_fp = re.findall('\://(.*?)$',
                                    self._value_ref_pattern)[0]

            except Exception:
                raise ValueError('Wrong value_ref_pattern')

            # Saving directory
            directory, file_pattern = os.path.split(dir_fp)

            # Suffix
            file_pattern, suffix = os.path.splitext(file_pattern)

            # Validate suffix
            # TODO: verified if the format is allowed by the plug-in
            suffix_valid = False
            for format, extensions in LIMA_EXT_FORMAT.items():
                if suffix in extensions:
                    suffix_valid = True
                    break
            if not suffix_valid:
                # TODO: Investigate if the acquisition fails in case of
                #  Exception
                raise ValueError('The extension used {} is not '
                                 'valid'.format(suffix))

            # Index format
            try:
                keywords = re.findall('{(.*?)}', file_pattern)
            except Exception:
                raise ValueError('Wrong value_ref_pattern')

            for keyword in keywords:
                key, value = keyword.split(':')
                if key.lower() == 'index':
                    value = value.split('d')[0]
                    index_format = '%{0}d'.format(value)
                    idx_fmt = value

            # Prefix
            try:
                prefix = re.findall('(.*?){', file_pattern)[0]
            except Exception:
                raise ValueError('Wrong value_ref_pattern')

            # Writing saving parameters
            self._limaccd.write_attribute('saving_directory', directory)
            self._limaccd.write_attribute('saving_format', format)
            self._limaccd.write_attribute('saving_suffix', suffix)
            self._limaccd.write_attribute('saving_index_format', index_format)
            if self._first_image_nb != 0:
                self._limaccd.write_attribute('saving_next_number', -1)
                time.sleep(0.05)
            self._limaccd.write_attribute('saving_prefix', prefix)
            # After to write the prefix with saving mode = ABORT,
            # the LimaCCDs takes some seconds to update the saving next
            # number, this time depends of the number of files on the folder
            # with the same pattern. For that reason the controller will
            # change the first saving next number on the first start.

            # Allow to set the First Image Number to any value different to
            # 0, default value on LimaCCDs after writing the prefix with
            # saving mode in Abort
            if self._first_image_nb != 0:
                t0 = time.time()
                saving_next_number = -1
                while saving_next_number == -1 and time.time() - t0 < 2.5:
                    saving_next_number = \
                        self._limaccd.read_attribute(
                            'saving_next_number').value
                    if saving_next_number == 0:
                        self._limaccd.write_attribute('saving_next_number',
                                                      self._first_image_nb)
                    time.sleep(0.03)

            scheme = 'file'
            if format == 'HDF5':
                scheme = 'h5file'
            image_pattern = '{scheme}://{dir}/{prefix}{{0:{idx_fmt}}}{suffix}'
            self._image_pattern = image_pattern.format(scheme=scheme,
                                                       dir=directory,
                                                       prefix=prefix,
                                                       idx_fmt=idx_fmt,
                                                       suffix=suffix)

        # Configure the acquisition if the synchronization mode is not
        # software trigger or software gate. For this case the acquisition
        # will configure point per point on load one
        if self._synchronization in [AcqSynch.SoftwareGate,
                                     AcqSynch.SoftwareTrigger]:
            return

        self._skipp_load = True
        self._skipp_start = True
        self.return_seq = True
        if self._synchronization == AcqSynch.SoftwareStart:
            acq_trigger_mode = 'Internal_trigger'
        elif self._synchronization == AcqSynch.HardwareStart:
            acq_trigger_mode = 'External_trigger'
        elif self._synchronization == AcqSynch.HardwareTrigger:
            acq_trigger_mode = 'External_trigger_multi'
        elif self._synchronization == AcqSynch.HardwareGate:
            acq_trigger_mode = 'External_gate'

        attrs_values = [['acq_expo_time', acq_expo_time],
                        ['acq_nb_frames', acq_nb_frames],
                        ['latency_time', latency_time],
                        ['acq_trigger_mode', acq_trigger_mode]]

        self._limaccd.write_attributes(attrs_values)

        self._prepareAcq()

    def LoadOne(self, axis, integ_time, repetitions, latency_time):
        self._log.debug("LoadOne t=%s rep=%s lat=%s",
            integ_time, repetitions, latency_time)
        if self._skipp_load:
            # PrepareOne configured the acquisition
            return

        # Configure acquisition for the case of Software Trigger/Gate.
        self._clean_variables()
        self.return_seq = False
        acq_nb_frames = 1
        acq_expo_time = integ_time
        latency_time = latency_time
        acq_trigger_mode = 'Internal_trigger'

        attrs_values = [['acq_expo_time', acq_expo_time],
                        ['acq_nb_frames', acq_nb_frames],
                        ['latency_time', latency_time],
                        ['acq_trigger_mode', acq_trigger_mode]]

        self._limaccd.write_attributes(attrs_values)
        self._prepareAcq()
        self._skipp_start = False
        self._started_flg = False

    def StartOne(self, axis, value):
        if self._skipp_start and self._started_flg:
            return

        self._log.debug("Start Acquisition")
        self._limaccd.startAcq()
        self._started_flg = True

        self._image_next_number = \
            self._limaccd.read_attribute('saving_next_number').value

    def ReadOne(self, axis):
        ready = self._limaccd.read_attribute('last_image_ready').value
        if not self.return_seq:
            # Case of use: synchronization by Software Trigger/Gate
            frames = read_lima_frames(self._limaccd, ready, ready + 1)
            return frames[0]
        else:
            if self._last_image_read == ready:
                return []
            frames = read_lima_frames(self._limaccd, self._last_image_read + 1, ready + 1)
            self._last_image_read += len(frames)
            self._image_next_number += len(frames)
            return frames

    def RefOne(self, axis):
        # TODO: check if it is possible to use last_image_saved instead of
        #  last_image_ready
        attr = 'last_image_saved'
        new_image_ready = self._limaccd.read_attribute(attr).value
        if not self.return_seq:
            # Case of use: synchronization by Software Trigger/Gate
            image_next_number = self._image_next_number + new_image_ready
            image_ref = self._image_pattern.format(image_next_number)
            return image_ref
        else:
            if self._last_image_read == new_image_ready:
                return []

            images_refs = []
            while self._last_image_read < new_image_ready:
                self._last_image_read += 1
                image_ref = self._image_pattern.format(self._image_next_number)
                self._image_next_number += 1
                images_refs.append(image_ref)
            return images_refs

    def StopOne(self, axis):
        self._log.debug('StopOne in')
        self._aborted_flg = True
        self._limaccd.stopAcq()

    def AbortOne(self, axis):
        self._log.debug('AbortOne in')
        self._aborted_flg = True
        # TODO: check if it is better to use stopAcq instead of abortAcq
        self._limaccd.abortAcq()

###############################################################################
#                Controller Extra Attribute Methods
###############################################################################
    def getSavingFormatsAllowed(self):
        modes = self._limaccd.command_inout('getAttrStringValueList',
                                            'saving_format')
        return modes

    def getSavingImageHeaders(self):
        raise RuntimeError('It is not possible to read the value')

    def setSavingImageHeaders(self, values):
        try:
            self._limaccd.resetCommonHeader()
            self._limaccd.resetFrameHeaders()
        except Exception as e:
            self._log.debug(
                "Lima version incompatible with reset header methods")
            self._log.debug(e)
        self._limaccd.setImageHeader(values)

    def SetCtrlPar(self, parameter, value, default_func):
        self._log.debug('SetCtrlPar %s %s' % (parameter, value))
        param = parameter.lower()
        if param in LIMA_ATTRS:
            attr = LIMA_ATTRS[param]
            self._log.debug('Set %s = %s' % (attr, value))
            self._limaccd.write_attribute(attr, value)
        else:
            default_func(parameter, value)

    def GetCtrlPar(self, parameter, default_func):
        param = parameter.lower()
        if param in LIMA_ATTRS:
            # TODO: Verify instrument_name attribute
            attr = LIMA_ATTRS[param]
            value = self._limaccd.read_attribute(attr).value
        else:
            value = default_func(parameter)

        return value

###############################################################################
#                Axis Extra Attribute Methods
###############################################################################

    def GetAxisPar(self, axis, parameter, default_func):
        if parameter == "value_ref_pattern":
            return self._value_ref_pattern
        elif parameter == "value_ref_enabled":
            return self._value_ref_enabled
        elif parameter == "shape":
            data = self._limaccd.read_attributes(("image_width", "image_height"))
            return data[0].value, data[1].value
        return default_func(axis, parameter)

    def SetAxisPar(self, axis, parameter, value, default_func):
        if parameter == "value_ref_pattern":
            self._value_ref_pattern = value
        elif parameter == "value_ref_enabled":
            self._value_ref_enabled = value
        else:
            default_func(axis, parameter, value)


class LimaCCDTwoDController(TwoDController, Referable):
    """
    Generic LimaCCD 2D Sardana Controller based on SEP2.
    """

    gender = "Lima"
    model = "2D"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    MaxDevice = 1

    ctrl_properties = {k: dict(v) for k, v in CTRL_PROPERTIES.items()}
    ctrl_attributes = {k: dict(v) for k, v in CTRL_ATTRIBUTES.items()}
    axis_attributes = {}

    def __init__(self, inst, props, *args, **kwargs):
        TwoDController.__init__(self, inst, props, *args, **kwargs)
        self._device = Lima(
            self.LimaCCDDeviceName, self.LatencyTime,
            self.FirstImageNumber, self._log
        )

    def GetAxisAttributes(self, axis):
        attrs = super().GetAxisAttributes(axis)
        attrs['Value'][MaxDimSize] = 2*[2**16]
        return attrs

    def AddDevice(self, axis):
        return self._device.AddDevice(axis)

    def StateOne(self, axis):
        return self._device.StateOne(axis)

    def PrepareOne(self, axis, value, repetitions, latency, nb_starts):
        return self._device.PrepareOne(axis, value, repetitions, latency, nb_starts)

    def LoadOne(self, axis, integ_time, repetitions, latency_time):
        return self._device.LoadOne(axis, integ_time, repetitions, latency_time)

    def StartOne(self, axis, value):
        return self._device.StartOne(axis, value)

    def ReadOne(self, axis):
        return self._device.ReadOne(axis)

    def RefOne(self, axis):
        return self._device.RefOne(axis)

    def StopOne(self, axis):
        self._device.StopOne(axis)

    def AbortOne(self, axis):
        self._device.AbortOne(axis)

    def getSavingFormatsAllowed(self):
        return self._device.getSavingFormatsAllowed()

    def getSavingImageHeaders(self):
        return self._device.getSavingImageHeaders()

    def setSavingImageHeaders(self, values):
        return self._device.setSavingImageHeaders(values)

    def SetCtrlPar(self, parameter, value):
        self._device.SetCtrlPar(parameter, value, super().SetCtrlPar)

    def GetCtrlPar(self, parameter):
        return self._device.GetCtrlPar(parameter, super().GetCtrlPar)

    def GetAxisPar(self, axis, parameter):
        return self._device.GetAxisPar(axis, parameter, super().GetAxisPar)

    def SetAxisPar(self, axis, parameter, value):
        self._device.SetAxisPar(axis, parameter, value, super().SetAxisPar)


class LimaCCDOneDController(OneDController, Referable):
    """
    Generic LimaCCD 1D Sardana Controller.
    """

    gender = "Lima"
    model = "1D"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    MaxDevice = 1

    ctrl_properties = {k: dict(v) for k, v in CTRL_PROPERTIES.items()}
    ctrl_attributes = {k: dict(v) for k, v in CTRL_ATTRIBUTES.items()}
    axis_attributes = {}

    def __init__(self, inst, props, *args, **kwargs):
        OneDController.__init__(self, inst, props, *args, **kwargs)
        self._device = Lima(
            self.LimaCCDDeviceName, self.LatencyTime,
            self.FirstImageNumber, self._log
        )

    def GetAxisAttributes(self, axis):
        attrs = super().GetAxisAttributes(axis)
        attrs['Value'][MaxDimSize] = [2**16]
        return attrs

    def AddDevice(self, axis):
        return self._device.AddDevice(axis)

    def StateOne(self, axis):
        return self._device.StateOne(axis)

    def PrepareOne(self, axis, value, repetitions, latency, nb_starts):
        return self._device.PrepareOne(axis, value, repetitions, latency, nb_starts)

    def LoadOne(self, axis, integ_time, repetitions, latency_time):
        return self._device.LoadOne(axis, integ_time, repetitions, latency_time)

    def StartOne(self, axis, value):
        return self._device.StartOne(axis, value)

    def ReadOne(self, axis):
        data = self._device.ReadOne(axis)
        if self._device.return_seq:
            for frame in data:
                frame.shape = frame.shape[0]
        else:
            data.shape = data.shape[0]
        return data

    def RefOne(self, axis):
        return self._device.RefOne(axis)

    def StopOne(self, axis):
        self._device.StopOne(axis)

    def AbortOne(self, axis):
        self._device.AbortOne(axis)

    def getSavingFormatsAllowed(self):
        return self._device.getSavingFormatsAllowed()

    def getSavingImageHeaders(self):
        return self._device.getSavingImageHeaders()

    def setSavingImageHeaders(self, values):
        return self._device.setSavingImageHeaders(values)

    def SetCtrlPar(self, parameter, value):
        self._device.SetCtrlPar(parameter, value, super().SetCtrlPar)

    def GetCtrlPar(self, parameter):
        return self._device.GetCtrlPar(parameter, super().GetCtrlPar)

    def GetAxisPar(self, axis, parameter):
        value = self._device.GetAxisPar(axis, parameter, super().GetAxisPar)
        if parameter.lower() == "shape":
            width, height = value
            assert height == 1
            value = [width]
        return value

    def SetAxisPar(self, axis, parameter, value):
        self._device.SetAxisPar(axis, parameter, value, super().SetAxisPar)
