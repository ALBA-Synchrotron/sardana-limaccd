
import PyTango
from sardana import State
from sardana.pool.controller import Type, Access, Description, Memorize, \
    Memorized, CounterTimerController, DataAccess
from sardana.pool import AcqSynch


class LimaRoICounterCtrl(CounterTimerController):
    """
    This class is the Tango Sardana CounterTimer controller for getting the
    Lima RoIs as counters

    Only Tested with LIMA CORE 1.7
    """

    gender = "LimaCounterTimer"
    model = "Basic"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    ctrl_properties = {
        'LimaROIDeviceName': {Type: str, Description: 'Name of the roicounter '
                                                      'lima device'},
        'LimaROIBufferSize': {Type: int, Description: 'Circular buffer size '
                                                      'in image'}
    }

    axis_attributes = {
        'RoIx1': {Type: int,
                  Description: 'Start pixel X',
                  Access: DataAccess.ReadWrite,
                  Memorize: Memorized
                  },
        'RoIx2': {Type: int,
                  Description: 'RoI width in X dimension (in pixels)',
                  Access: DataAccess.ReadWrite,
                  Memorize: Memorized
                  },
        'RoIy1': {Type: int,
                  Description: 'Start pixel Y',
                  Access: DataAccess.ReadWrite,
                  Memorize: Memorized
                  },
        'RoIy2': {Type: int,
                  Description: 'RoI hight in Y dimension (in pixels)',
                  Access: DataAccess.ReadWrite,
                  Memorize: Memorized
                  },
    }

    # The command readCounters returns roi_id,frame number, sum, average, std,
    # min, max, ...
    IDX_ROI_ID = 0
    IDX_IMAGE_NR = 1
    IDX_SUM = 2
    IDX_AVERAGE = 3
    IDX_STD_DEVIATION = 4
    IDX_MIN_PIXEL = 5
    IDX_MAX_PIXEL = 6

    def __init__(self, inst, props, *args, **kwargs):
        CounterTimerController.__init__(self, inst, props, *args, **kwargs)
        self._log.debug("__init__(%s, %s): Entering...", repr(inst),
                        repr(props))

        try:
            self._limaroi = PyTango.DeviceProxy(self.LimaROIDeviceName)
        except PyTango.DevFailed as e:
            raise RuntimeError('__init__(): Could not create a device proxy '
                               'from following device name: %s.\nException: '
                               '%s ' % (self.LimaROIDeviceName, e))

        self._limaroi.Stop()
        self._limaroi.clearAllRois()
        self._limaroi.Start()
        self._limaroi.write_attribute('BufferSize', self.LimaROIBufferSize)
        self._rois = {}
        self._rois_id = {}
        self._data_buff = {}
        self._state = None
        self._status = None
        self._repetitions = 0
        self._last_image_read = -1
        self._last_image_ready = -1
        self._start = False
        self._synchronization = AcqSynch.SoftwareTrigger
        self._abort_flg = False

        # event_type = PyTango.EventType.PERIODIC_EVENT
        # self._callback_id = self._limaroi.subscribe_event('state',
        #                                                   event_type,
        #                                                   self._callback)
        self._log.debug("__init__(%s, %s): Leaving...", repr(inst),
                        repr(props))

    def _callback(self, event):
        if event.err:
            self._log.debug("Detected LimaROI DS reconnection, applying ROIS")
            self._recreate_rois()

    def _clean_acquisition(self):
        if self._last_image_read != -1:
            self._last_image_read = -1
            self._last_image_ready = -1
            self._repetitions = 0
            self._start = False
            self._abort_flg = False

    def _recreate_rois(self):
        state = self._limaroi.state()
        if state == 'ON':
            return
        self._limaroi.Start()
        for axis in list(self._rois.keys()):
            self._create_roi(axis)
        self._recreate_flg = True

    def _create_roi(self, axis):
        roi_name = self._rois[axis]['name']
        roi_id = int(self._limaroi.addNames([roi_name])[0])
        self._rois[axis]['id'] = roi_id
        self._rois_id[roi_id] = axis
        roi = [roi_id] + self._rois[axis]['roi']
        self._limaroi.setRois(roi)

    def AddDevice(self, axis):
        self._rois[axis] = {}
        roi_name = 'roi_%d' % axis
        self._rois[axis]['name'] = roi_name
        self._rois[axis]['roi'] = [0, 0, 1, 1]
        self._data_buff[axis] = []
        self._create_roi(axis)

    def DeleteDevice(self, axis):
        self._data_buff.pop(axis)
        self._rois.pop(axis)
        roi_id = self._rois[axis]['id']
        self._rois_id.pop(roi_id)

    def StateAll(self):
        attr = 'CounterStatus'
        if self._abort_flg:
            self._state = State.On
            self._status = 'Aborted'
            self._log.debug('StateAll: [%s] %s' % (self._state, self._status))
            return

        self._last_image_ready = self._limaroi.read_attribute(attr).value
        if (self._last_image_ready < (self._repetitions - 1) and
                self._last_image_ready != -2):
            self._state = State.Moving
            self._status = 'Taking data'
        else:
            self._state = State.On
            # self._clean_acquisition()
            if self._last_image_ready == -2:
                self._status = "Not images in buffer"
            else:
                self._status = "RoI computed"
        self._log.debug('StateAll: [%s] %s' % (self._state, self._status))

    def StateOne(self, axis):
        return self._state, self._status

    def LoadOne(self, axis, value, repetitions, latency):
        self._clean_acquisition()
        if self._synchronization == AcqSynch.SoftwareTrigger:
            self._repetitions = 1
        elif self._synchronization == AcqSynch.HardwareTrigger:
            self._repetitions = repetitions
        else:
            raise ValueError('LimaRoICoTiCtrl allows only Software or '
                             'Hardware triggering')

    def StartAll(self):
        self._start = True
        self._abort_flg = False

    def AbortOne(self, axis):
        self._abort_flg = True

    def ReadAll(self):
        for axis in list(self._data_buff.keys()):
            self._data_buff[axis] = []
        if self._last_image_ready != self._last_image_read:
            if not self._synchronization == AcqSynch.SoftwareTrigger:
                self._last_image_read += 1
            rois_data = self._limaroi.readCounters(self._last_image_read)
            self._last_image_ready = rois_data[-6]
            for base_idx in range(0, len(rois_data), 7):
                roi_id_idx = base_idx + self.IDX_ROI_ID
                roi_id = rois_data[roi_id_idx]
                axis = self._rois_id[roi_id]
                if axis in self._data_buff:
                    sum_idx = base_idx + self.IDX_SUM
                    self._data_buff[axis] += [rois_data[sum_idx]]
            self._log.debug('Read images [%d, %d]' % (self._last_image_read,
                                                      self._last_image_ready))
            if self._synchronization in [AcqSynch.HardwareTrigger,
                                         AcqSynch.HardwareGate]:
                self._last_image_read = self._last_image_ready

    def ReadOne(self, axis):
        try:
            if self._synchronization == AcqSynch.SoftwareTrigger:
                # ReadOne is called even before the acquisition or the
                # calculation has finished because ctctrl reads to have
                # readings of the counter evolution
                if len(self._data_buff[axis]) == 0:
                    raise Exception('Acquisition did not finish correctly.')
                value = int(self._data_buff[axis][0])
            else:
                value = self._data_buff[axis]
        except Exception as e:
            self._log.error("ReadOne %r" % e)
        self._log.debug("ReadOne return %r" % value)
        return value

    def GetAxisExtraPar(self, axis, name):
        name = name.lower()
        result = None
        if 'roi' in name:
            roi = self._rois[axis]['roi']
            if name == "roix1":
                result = roi[0]
            elif name == "roix2":
                result = roi[2]
            elif name == "roiy1":
                result = roi[1]
            elif name == "roiy2":
                result = roi[3]
        return result

    def SetAxisExtraPar(self, axis, name, value):
        name = name.lower()
        if 'roi' in name:
            roi = self._rois[axis]['roi']
            if name == "roix1":
                roi[0] = value
            elif name == "roix2":
                roi[2] = value
            elif name == "roiy1":
                roi[1] = value
            elif name == "roiy2":
                roi[3] = value
            self._rois[axis]['roi'] = roi
            roi_id = self._rois[axis]['id']
            new_roi = [roi_id] + roi
            self._limaroi.setRois(new_roi)
