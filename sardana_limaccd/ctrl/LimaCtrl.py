from sardana import State
from sardana.pool.controller import TwoDController, OneDController, \
    Type, Description, Access, DataAccess, Memorize, NotMemorized, Memorized, \
    DefaultValue, MaxDimSize, AcqSynch, Referable

from sardana_limaccd.ctrl.LimaCCDCtrl import (
    CTRL_PROPERTIES, CTRL_ATTRIBUTES, LIMA_ATTRS, LIMA_EXT_FORMAT
)

from sardana_limaccd.client import Lima


__all__ = ['Lima2DCtrl', 'Lima1DCtrl']


TRIGGER_MAP = {
    AcqSynch.SoftwareStart: "INTERNAL_TRIGGER_MULTI",
    AcqSynch.SoftwareGate:  "INTERNAL_TRIGGER_MULTI",
    AcqSynch.HardwareStart: "EXTERNAL_TRIGGER",
    AcqSynch.HardwareTrigger: "EXTERNAL_TRIGGER_MULTI",
    AcqSynch.HardwareGate: "EXTERNAL_GATE"
}


STATUS_MAP = {
    "Ready": (State.On, 'The LimaCCD is ready to acquire'),
    "Running": (State.Moving, 'The LimaCCD is acquiring'),
}


def saving_for_pattern(pattern):
    # TODO: Improve regexp matching
    # Extract saving configuration from the pattern
    try:
        dir_fp = re.findall('\://(.*?)$', pattern)[0]
    except Exception:
        raise ValueError('Wrong value_ref_pattern')

    # Saving directory
    directory, file_pattern = os.path.split(dir_fp)

    # Suffix
    file_pattern, suffix = os.path.splitext(file_pattern)

    # Validate suffix
    # TODO: verified if the format is allowed by the plug-in
    suffix_valid = False
    for fmt, extensions in LIMA_EXT_FORMAT.items():
        if suffix in extensions:
            suffix_valid = True
            break
    if not suffix_valid:
        # TODO: Investigate if the acquisition fails in case of
        #  Exception
        raise ValueError('The extension used {} is not valid'.format(suffix))

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

    return {
        "saving_directory": directory,
        "saving_format": fmt,
        "saving_index_format": index_format,
        "saving_suffix": suffix,
        "saving_prefix": prefix,
    }


def get_ctrl_par(ctrl, name):
    param = LIMA_ATTRS.get(name.lower())
    if param is None:
        return super(ctrl._klass, ctrl).GetCtrlPar(name)
    else:
        return ctrl._lima[param]


def set_ctrl_par(ctrl, name, value):
    ctrl._log.debug('SetCtrlPar %s %s' % (name, value))
    param = LIMA_ATTRS.get(name.lower())
    if param is None:
        super(ctrl._klass, ctrl).SetCtrlPar(name, value)
    else:
        ctrl._lima[param] = value


def get_axis_par(ctrl, axis, name):
    name = name.lower()
    if name == "value_ref_pattern":
        return ctrl._value_ref_pattern
    elif name == "value_ref_enabled":
        return ctrl._value_ref_enabled
    elif name == "shape":
        return ctrl._lima["image_width", "image_height"]
    return super(ctrl._klass, ctrl).GetAxisPar(axis, name)


def set_axis_par(ctrl, axis, name, value):
    if name == "value_ref_pattern":
        ctrl._value_ref_pattern = value
    elif name == "value_ref_enabled":
        ctrl._value_ref_enabled = value
    else:
        super(ctrl._klass, ctrl).SetAxisPar(axis, name, value)


class Lima2DCtrl(TwoDController, Referable):
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
        self._lima = Lima(self.LimaCCDDeviceName,  self._log)
        # for old python 2.7 where super() does not work
        self._klass = Lima2DCtrl
        self._acquisition = None

    def GetAxisAttributes(self, axis):
        # make sure any lima image size can be used
        attrs = super().GetAxisAttributes(axis)
        attrs['Value'][MaxDimSize] = 2*[2**16]
        return attrs

    def StateAll(self):
        status = "Ready" if self._acquisition is None else self._acquisition.status
        state = STATUS_MAP.get(status)
        if state is None:
            state = State.Fault, 'The LimaCCD state is: {}'.format(status)
        return state

    def StateOne(self, axis):
        return self.__status

    def PrepareOne(self, axis, expo_time, repetitions, latency_time, nb_starts):
        nb_frames = repetitions * nb_starts
        trigger_mode = TRIGGER_MAP[self._synchronization]
        if trigger_mode == "INTERNAL_TRIGGER_MULTI":
            if trigger_mode not in self._lima.capabilities["acq_trigger_mode"]:
                trigger_mode = "INTERNAL_TRIGGER"
        self._acquisition = Acquisition(
            nb_frames, expo_time, latency, trigger_mode)

        if self._value_ref_enabled:
            config = {
                "saving_stream_active": True,
                "saving_mode": "AUTO_FRAME",
                "saving_overwrite_policy": "ABORT",
            }
            config.update(saving_for_pattern(self._value_ref_pattern))
            self._lima.saving.config.update(config)
            self._lima.saving.prepare()

        if trigger_mode != "INTERNAL_TRIGGER":
            self._acquisition.prepare()

    def LoadOne(self, axis, expo_time, repetitions, latency_time):
        if self._acquisition["acq_trigger_mode"] != "INTERNAL_TRIGGER":
            return
        self._acquisition.config.update({
            "acq_expo_time": expo_time,
            "acq_nb_frames": 1,
            "latency_time": latency_time
        })
        self._acquisition.prepare()

    def StartOne(self, axis, value):
        pass

    def StartAll(self):
        if not "INTERNAL" in self._acquisition["acq_trigger_mode"]:
            return
        self._acquisition.start()

    def ReadAll(self):
        if self._synchronization in {AcqSynch.SoftwareGate, AcqSynch.SoftwareTrigger}:
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
        if self._synchronization in {AcqSynch.SoftwareGate, AcqSynch.SoftwareTrigger}:
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
        return get_ctrl_par(self, name)

    def SetCtrlPar(self, name, value):
        set_ctrl_par(self, name, value)

    def GetAxisPar(self, axis, name):
        return get_axis_par(self, axis, name)

    def SetAxisPar(self, axis, name, value):
        set_ctrl_par(self, name, value)



