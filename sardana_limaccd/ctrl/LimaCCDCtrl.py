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

from sardana import State
from sardana.pool.controller import TwoDController, OneDController, \
    Type, Description, Access, DataAccess, Memorize, NotMemorized, Memorized, \
    DefaultValue, MaxDimSize, AcqSynch, Referable

from sardana_limaccd.client import Lima, Acquisition


__author__ = 'Roberto Javier Homs Puron'
__copyright__ = 'Copyright 2019 CELLS / ALBA Synchrotron, Bellaterra, Spain'
__docformat__ = "restructuredtext"

__all__ = ['LimaCCDTwoDController', 'LimaCCDOneDController']


LIMA_ATTRS = {
    'cameramodel': 'camera_model',
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
    'savingsuffix': 'saving_suffix'
}


LIMA_EXT_FORMAT = {
    'EDF': ['.edf'],
    'HDF5': ['.h5', '.hdf5'],
    'CBF': ['.cbf'],
    'TIFF': ['.tiff']
}


TRIGGER_MAP = {
    AcqSynch.SoftwareStart: "INTERNAL_TRIGGER",
    AcqSynch.SoftwareTrigger: "INTERNAL_TRIGGER_MULTI",
    AcqSynch.SoftwareGate: "INTERNAL_TRIGGER_MULTI",
    AcqSynch.HardwareStart: "EXTERNAL_TRIGGER",
    AcqSynch.HardwareTrigger: "EXTERNAL_TRIGGER_MULTI",
    AcqSynch.HardwareGate: "EXTERNAL_GATE"
}


STATUS_MAP = {
    "Ready": (State.On, 'The LimaCCD is ready to acquire'),
    "Running": (State.Moving, 'The LimaCCD is acquiring'),
}


class LimaCtrlMixin(object):
    """
    Generic LimaCCD 2D Sardana Controller based on SEP2.
    """

    gender = "Lima"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    ctrl_properties = {
        'LimaCCDDeviceName': {
            Type: str, Description: 'Detector device name'
        },
        'LatencyTime': {
            Type: float,
            Description: 'Maximum latency time'
        },
        'FirstImageNumber': {
            Type: int,
            DefaultValue: 0,
            Description: 'First value of the saving next number'
        },
        'FirstImageNumberDelayTime': {
            Type: float,
            DefaultValue: 0.05,
            Description: 'Sleep time required to let Lima set the first image '
                         'number'
        },
        'MasterTrigger': {
            Type: bool,
            DefaultValue: False,
            Description: 'True if detector is master trigger or'
                         'False otherwise'
        }
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

    axis_attributes = {}

    def __init__(self, ctrl_class):
        self._ctrl_class = ctrl_class
        self._lima = Lima(self.LimaCCDDeviceName,  self._log)
        self._lima.saving.first_image_nb = self.FirstImageNumber
        self._lima.saving.delay_time = self.FirstImageNumberDelayTime
        self._latency_time = self.LatencyTime
        self._acquisition = None
        try:
            self._lima("reset")
        except Exception:
            self._log.exception("Failed to initialize")

    @property
    def is_soft_gate_or_trigger(self):
        return self._synchronization in \
            {AcqSynch.SoftwareGate, AcqSynch.SoftwareTrigger}

    @property
    def is_start_synch(self):
        return self._synchronization in \
            {AcqSynch.SoftwareStart, AcqSynch.HardwareStart}

    def calc_latency(self, custom_latency):
        if self.MasterTrigger and not self.is_start_synch:
            return custom_latency
        else:
            return self._latency_time

    def calc_trigger_mode(self):
        trigger_mode = TRIGGER_MAP[self._synchronization]
        if trigger_mode == "INTERNAL_TRIGGER_MULTI":
            if trigger_mode not in self._lima.capabilities["acq_trigger_mode"]:
                trigger_mode = "INTERNAL_TRIGGER"
        elif trigger_mode not in self._lima.capabilities["acq_trigger_mode"]:
            raise ValueError("trigger mode {0} not supported".format(trigger_mode))
        return trigger_mode

    def StateAll(self):
        acq = self._acquisition
        status_info = self._lima.get_status()
        acq_status, ready_for_next, idx_ready, idx_saved = status_info
        status = acq_status if acq is None else acq.calc_status(*status_info)
        state = STATUS_MAP.get(status)
        if state is None:
            state = State.Fault, 'The LimaCCD state is: {0}'.format(status)
        self._log.debug(
            "StateAll: status=%s acq=%s next_ready=%s, "
            "idx_acq=%s, idx_saved=%s", status, *status_info
        )
        self.__state = state

    def StateOne(self, axis):
        return self.__state

    def PrepareOne(self, axis, expo_time, repetitions, latency_time, nb_starts):
        self._log.info(
            'PrepareOne axis=%s exposure=%s rep=%s lat=%s nb_starts=%s sync=%s',
            axis, expo_time, repetitions, latency_time, nb_starts,
            self._synchronization)
        lima = self._lima
        if lima["acq_status"] != "Ready":
            lima("stopAcq")
        latency_time = self.calc_latency(latency_time)
        trigger_mode = self.calc_trigger_mode()

        self._acquisition = lima.acquisition(
            repetitions, nb_starts, expo_time, latency_time, trigger_mode)

        lima.saving.prepare()
        if not self._acquisition.trigger.is_internal_start:
            self._acquisition.prepare()

    def LoadOne(self, axis, expo_time, repetitions, latency_time):
        self._log.info(
            'LoadOne axis=%s exposure=%s rep=%s lat=%s sync=%s',
            axis, expo_time, repetitions, latency_time,
            self._synchronization)
        if self._acquisition.trigger.is_internal_start:
            latency_time = self.calc_latency(latency_time)
            self._acquisition = self._lima.acquisition(
                repetitions, 1, expo_time, latency_time, "INTERNAL_TRIGGER")
            self._acquisition.prepare()

    def StartOne(self, axis, value):
        pass

    def StartAll(self):
        self._log.info("StartAll trig=%s", self._acquisition.trigger.mode)
        self._acquisition.start()

    def ReadAll(self):
        if self.is_soft_gate_or_trigger:
            store = self._acquisition.next_frame()
        else:
            store = self._acquisition.next_frames()
        self._acquisition.store = store

    def ReadOne(self, axis):
        result = self._acquisition.store
        # remove reference to frame after read
        del self._acquisition.store
        return result

    def RefOne(self, axis):
        if self.is_soft_gate_or_trigger:
            res = self._acquisition.next_ref_frame()
        else:
            res = self._acquisition.next_ref_frames()
        return res

    def StopOne(self, axis):
        if self._acquisition:
            self._acquisition.stop()
        else:
            self._lima("stopAcq")

    def AbortOne(self, axis):
        if self._acquisition:
            self._acquisition.abort()
        else:
            self._lima("abortAcq")

    def getSavingFormatsAllowed(self):
        return self._lima.capabilities["saving_format"]

    def getSavingImageHeaders(self):
        return self._lima.getSavingImageHeaders()

    def setSavingImageHeaders(self, values):
        return self._lima.setSavingImageHeaders(values)

    def GetCtrlPar(self, name):
        param = LIMA_ATTRS.get(name.lower())
        if param is None:
            return self._ctrl_class.GetCtrlPar(self, name)
        else:
            return self._lima[param]

    def SetCtrlPar(self, name, value):
        self._log.debug('SetCtrlPar %s %s' % (name, value))
        param = LIMA_ATTRS.get(name.lower())
        if param is None:
            self._ctrl_class.SetCtrlPar(self, name, value)
        else:
            self._lima[param] = value

    def GetAxisPar(self, axis, name):
        name = name.lower()
        if name == "value_ref_pattern":
            return self._lima.saving.pattern
        elif name == "value_ref_enabled":
            return self._lima.saving.enabled
        elif name == "shape":
            return self._lima["image_width", "image_height"]
        return self._ctrl_class.GetAxisPar(self, axis, name)

    def SetAxisPar(self, axis, name, value):
        if name == "value_ref_pattern":
            self._lima.saving.pattern = value
        elif name == "value_ref_enabled":
            self._lima.saving.enabled = value
        else:
            self._ctrl_class.SetAxisPar(self, axis, name, value)

    def GetAxisAttributes(self, axis):
        # make sure any lima image size can be used
        attrs = self._ctrl_class.GetAxisAttributes(self, axis)
        attrs['Value'][MaxDimSize] = self.MaxDimSize
        return attrs


class LimaCCDOneDController(LimaCtrlMixin, OneDController, Referable):
    """
    Generic LimaCCD 1D Sardana Controller.

    Each axis represents one row in the acquired image
    (where axis=N reads row=N-1)

    Based on the requirements for mythen (dectris) and
    Xspress3 (quantum detectors)
    """

    model = "1D"
    MaxDevice = 2**14
    MaxDimSize = [2**16]

    def __init__(self, inst, props, *args, **kwargs):
        OneDController.__init__(self, inst, props, *args, **kwargs)
        LimaCtrlMixin.__init__(self, OneDController)
        # Disable ref_enabled until
        # https://github.com/sardana-org/sardana/issues/1445
        self._lima.saving.enabled = False

    def ReadOne(self, axis):
        acq = self._acquisition
        if self.is_soft_gate_or_trigger:
            return None if acq.store is None else acq.store[axis - 1]
        else:
            return [frame[axis - 1] for frame in acq.store]

    def SetAxisPar(self, axis, name, value):
        if name.lower() == "value_ref_enabled" and value:
            raise ValueError("Cannot set value_ref_enabled on 1D")
        LimaCtrlMixin.SetAxisPar(self, axis, name, value)

    def GetAxisPar(self, axis, name):
        res = LimaCtrlMixin.GetAxisPar(self, axis, name)
        if name.lower() == "shape":
            res = [res[0]]
        return res


class LimaCCDTwoDController(LimaCtrlMixin, TwoDController, Referable):
    """
    Generic LimaCCD 2D Sardana Controller based on SEP2.
    """

    model = "2D"
    MaxDevice = 1
    MaxDimSize = 2*[2**16]

    def __init__(self, inst, props, *args, **kwargs):
        TwoDController.__init__(self, inst, props, *args, **kwargs)
        LimaCtrlMixin.__init__(self, TwoDController)
        self._lima.saving.enabled = True

    def SetAxisPar(self, axis, name, value):
        # Don't support value_ref_enabled = False in 2D
        if name.lower() == "value_ref_enabled" and not value:
            raise ValueError("Cannot disable value_ref_enabled on 2D")
        LimaCtrlMixin.SetAxisPar(self, axis, name, value)
