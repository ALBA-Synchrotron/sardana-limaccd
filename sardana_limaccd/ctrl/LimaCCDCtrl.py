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
import PyTango
from sardana import State
from sardana.pool.controller import TwoDController, Type, Description, \
    Access, DataAccess, Memorize, NotMemorized, Memorized, DefaultValue, \
    MaxDimSize, AcqSynch, Referable


__author__ = 'Roberto Javier Homs Puron'
__copyright__ = 'Copyright 2019 CELLS / ALBA Synchrotron, Bellaterra, Spain'
__docformat__ = "restructuredtext"

__all__ = ['LimaCCDTwoDController']


LIMA_ATTRS = {'cameramode': 'camera_mode',
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


class LimaCCDTwoDController(TwoDController, Referable):
    """
    Generic LimaCCD 2D Sardana Controller based on SEP2. This controller
    will work only with reference value as first version. That is why the
    method ReadOne is not implemented and it is not possible to
    use pseudo-counter.
    """

    gender = "LimaController"
    model = "Basic"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    MaxDevice = 1

    ctrl_properties = {
        'LimaCCDDeviceName': {Type: str, Description: 'Detector device name'},
        'LatencyTime': {Type: float,
                        Description: 'Maximum latency time'},
        }

    ctrl_attributes = {
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
                         'set it to MANUAL on the startup.',
            Access: DataAccess.ReadWrite,
            Memorize: NotMemorized},
        'SavingNextNumber': {
            Type: int,
            Description: 'LimaCCD attribute saving_next_number',
            Access: DataAccess.ReadWrite,
            Memorize: Memorized},
        'SavingOverwritePolicy': {
            Type: str,
            Description: 'LimaCCD attribute saving_overwrite_policy',
            Access: DataAccess.ReadWrite,
            Memorize: Memorized},
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

    axis_attributes = {}

    def __init__(self, inst, props, *args, **kwargs):
        TwoDController.__init__(self, inst, props, *args, **kwargs)
        # self._log.debug("__init__(%s, %s): Entering...", repr(inst),
        #                 repr(props))

        try:
            self._limaccd = PyTango.DeviceProxy(self.LimaCCDDeviceName)
            self._limaccd.reset()
        except PyTango.DevFailed as e:
            raise RuntimeError('__init__(): Could not create a device proxy '
                               'from following device name: %s.\nException: '
                               '%s ' % (self.LimaCCDDeviceName, e))

        self._latency_time = self.LatencyTime
        self._synchronization = AcqSynch.SoftwareTrigger
        self._value_ref_pattern = ''
        self._value_ref_enabled = False
        self._skipp_load = False
        self._skipp_start = False
        self._return_seq = False
        self._last_image_read = -1
        self._image_next_number = 0
        self._new_data = False
        self._aborted_flg = False
        self._started_flg = False
        self._image_pattern = ''
        self._nb_frames = 0

        # Get the Detector Saving Modes allowed
        formats = self._limaccd.command_inout('getAttrStringValueList',
                                              'saving_format')
        self._saving_formats_allowed = formats

    def _clean_variables(self):
        self._skipp_load = False
        self._skipp_start = False
        self._return_seq = False
        self._new_data = False
        self._aborted_flg = False
        self._started_flg = False
        self._last_image_read = -1
        self._image_next_number = 0
        self._nb_frames = 0

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

    def StateOne(self, axis):
        acq_ready = self._limaccd.read_attribute('acq_status').value

        attr = 'last_image_saved'
        new_image_ready = self._limaccd.read_attribute(attr).value

        images_to_save = False
        if new_image_ready == -1 or new_image_ready < self._nb_frames - 1:
            images_to_save = True

        if acq_ready not in ['Ready', 'Running']:
            state = State.Fault
            status = 'The LimaCCD state is: {0}'.format(acq_ready)
        elif acq_ready == 'Running' or images_to_save:
            state = State.Moving
            status = 'The LimaCCD is acquiring'
        else:
            state = State.On
            status = 'The LimaCCD is ready to acquire'

        self._log.debug('Status: {0}'.format(status))
        return state, status

    def PrepareOne(self, axis, value, repetitions, latency, nb_starts):
        self._clean_variables()

        acq_nb_frames = repetitions * nb_starts
        self._nb_frames = acq_nb_frames
        acq_expo_time = value
        latency_time = latency
        # Configure saving
        if self._value_ref_enabled:
            self._limaccd.write_attribute('saving_mode', 'AUTO_FRAME')

            # TODO: Improve regexp matching

            # Configure directory
            try:
                dir_fp = re.findall('\://(.*?)$',
                                    self._value_ref_pattern)[0]

            except Exception:
                raise ValueError('Wrong value_ref_pattern')

            directory, file_pattern = os.path.split(dir_fp)
            self._limaccd.write_attribute('saving_directory', directory)

            file_pattern, suffix = os.path.splitext(file_pattern)

            # Configure saving format:
            # TODO: verified if the format is allowed by the plug-in
            suffix_valid = False
            for format, extensions in LIMA_EXT_FORMAT.items():
                if suffix in extensions:
                    suffix_valid = True
                    break
            if not suffix_valid:
                raise ValueError('The extension used {} is not '
                                 'valid'.format(suffix))
            self._limaccd.write_attribute('saving_format', format)
            self._limaccd.write_attribute('saving_suffix', suffix)

            # Extract the index format
            try:
                keywords = re.findall('{(.*?)}', file_pattern)
            except Exception:
                raise ValueError('Wrong value_ref_pattern')

            for keyword in keywords:
                key, value = keyword.split(':')
                if key.lower() == 'index':
                    index_format = '%{0}d'.format(value)
                    idx_fmt = value
                    self._limaccd.write_attribute('saving_index_format',
                                                  index_format)

            # Extract the index format
            try:
                prefix = re.findall('(.*?){', file_pattern)[0]
            except Exception:
                raise ValueError('Wrong value_ref_pattern')

            self._limaccd.write_attribute('saving_prefix', prefix)

            # TODO: include scheme
            image_pattern = '{dir}/{prefix}{{0:{idx_fmt}}}{suffix}'
            self._image_pattern = image_pattern.format(dir=directory,
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
        self._return_seq = True
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
        self._image_next_number = \
            self._limaccd.read_attribute('saving_next_number').value

    def LoadOne(self, axis, integ_time, repetitions, latency_time):

        if self._skipp_load:
            # PrepareOne configured the acquisition
            return

        # Configure acquisition for the case of Software Trigger/Gate.
        self._clean_variables()
        self._return_seq = False
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
        self._image_next_number = \
            self._limaccd.read_attribute('saving_next_number').value
        self._skipp_start = False
        self._started_flg = False

    def StartOne(self, axis, value):
        if self._skipp_start and self._started_flg:
            return

        self._log.debug("Start Acquisition")
        self._limaccd.startAcq()
        self._started_flg = True

    def ReadOne(self, axis):
        # TODO: Implement on future version
        raise NotImplementedError()

    def RefOne(self, axis):
        # TODO: check if it is possible to use last_image_saved instead of
        #  last_image_ready
        attr = 'last_image_saved'
        new_image_ready = self._limaccd.read_attribute(attr).value
        if not self._return_seq:
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

    def SetCtrlPar(self, parameter, value):
        self._log.debug('SetCtrlPar %s %s' % (parameter, value))
        param = parameter.lower()
        if param in LIMA_ATTRS:
            attr = LIMA_ATTRS[param]
            self._log.debug('Set %s = %s' % (attr, value))
            self._limaccd.write_attribute(attr, value)
        else:
            super(LimaCCDTwoDController, self).SetCtrlPar(parameter, value)

    def GetCtrlPar(self, parameter):
        param = parameter.lower()
        if param in LIMA_ATTRS:
            # TODO: Verify instrument_name attribute
            attr = LIMA_ATTRS[param]
            value = self._limaccd.read_attribute(attr).value
        else:
            value = super(LimaCCDTwoDController, self).GetCtrlPar(parameter)

        return value

###############################################################################
#                Axis Extra Attribute Methods
###############################################################################

    def SetAxisPar(self, axis, parameter, value):
        if parameter == "value_ref_pattern":
            self._value_ref_pattern = value
        elif parameter == "value_ref_enabled":
            self._value_ref_enabled = value
