import time
import PyTango
from sardana import State
from sardana.pool.controller import Type, Access, Description, Memorize, \
    Memorized, CounterTimerController, DataAccess, DefaultValue
from sardana.pool import AcqSynch

SOFTWARE_SYNC = [AcqSynch.SoftwareTrigger, AcqSynch.SoftwareGate]
HARDWARE_SYNC = [AcqSynch.HardwareTrigger, AcqSynch.HardwareGate]



class LimaXspress3ScalarsCtrl(CounterTimerController):
    """
    TODO:
    """

    gender = "LimaCounterTimer"
    model = "Basic"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    ctrl_properties = {
        'LimaCCDsDeviceName': {Type: str,
                               Description: 'LimaCCDs device name'},
    }

    axis_attributes = {
        'channel': {Type: int,
                    Description: 'Channel to read scalars',
                    Access: DataAccess.ReadWrite,
                    Memorize: Memorized
                    },
        'scalar_index': {Type: int,
                         Description: 'Position index [0,10]',
                         Access: DataAccess.ReadWrite,
                         Memorize: Memorized
                        },

    }
    def __init__(self, inst, props, *args, **kwargs):
        CounterTimerController.__init__(self, inst, props, *args, **kwargs)

        self.limaccd = PyTango.DeviceProxy(self.LimaCCDsDeviceName)
        plugins = self.limaccd['plugin_list'].value
        if 'xspress3' not in plugins:
            raise RuntimeError('The Lima DS is not compatible with Xspress3')
        idx = plugins.index('xspress3')
        xspress3_name = plugins[idx + 1]
        self.xspress3 = PyTango.DeviceProxy(xspress3_name)

        self.nr_channels = self.xspress3.read_attribute('numChan').value
        self.config = {}
        self.started = {}
        self.aborted = False
        self.repetitions = 0
        self.last_image_read = -1
        self.last_image_ready = -1
        self.start = False
        self._synchronization = AcqSynch.SoftwareTrigger

    def AddDevice(self, axis):
        self.config[axis] = {'data':[], 'channel':0, 'index':9}

    def StateAll(self):
        if self.aborted:
            self.state = State.On
            self.status = 'Aborted'
            self._log.debug('StateAll aborted: state %s  status %s',
                            self.state, self.status)
            return
        attr = 'last_image_ready'
        self.last_image_ready = self.limaccd.read_attribute(attr).value
        if self.last_image_ready < (self.repetitions - 1):
            self.state = State.Moving
            self.status = 'Taking data'
        else:
            self.state = State.On
            self.status = 'ON'
        self._log.debug('StateAll: state %s  status %s', self.state,
                        self.status)

    def StateOne(self, axis):
        return self.state, self.status

    def LoadOne(self, axis, value, repetitions, latency):
        self._clean_acquisition()
        if self._synchronization in SOFTWARE_SYNC:
            self.repetitions = 1
        elif self._synchronization in HARDWARE_SYNC:
            self.repetitions = repetitions
        else:
            raise ValueError('LimaXspress3CoTiCtrl allows only Software or '
                             'Hardware triggering')

    def PreStartOne(self, axis, value):
        channel = self.config[axis]['channel']
        if channel not in self.started:
            self.started[channel] = []
        self.started[channel].append(axis)
        return True

    def StartAll(self):
        self.start = True
        self.aborted = False

    def AbortOne(self, axis):
        self.aborted = True

    def ReadAll(self):
        self._clean_data()
        self.last_image_ready = self.limaccd.read_attribute(
            'last_image_ready').value
        self._log.debug('ReadAll: ready %s read %s repetitions %s',
                        self.last_image_ready, self.last_image_read,
                        self.repetitions)

        if self.last_image_read > self.last_image_ready:
            return

        if (self.last_image_read == self.last_image_ready and
                self.repetitions > 1):
            return

        if self.last_image_ready > -1 and \
                self.last_image_read == self.last_image_ready and \
                self.repetitions == 1:
            self._get_values(0)
            self.last_image_read = 1
            return
        elif self._synchronization in HARDWARE_SYNC:
            self.last_image_read += 1
            self._log.debug('Reading from %s to %s', self.last_image_read,
                            self.last_image_ready)

            nr_images = self.last_image_ready - self.last_image_read + 1
            for i in range(nr_images):
                image_nr = self.last_image_read + i
                self._get_values(image_nr)
            self.last_image_read = self.last_image_ready

    def ReadOne(self, axis):
        if self._synchronization in SOFTWARE_SYNC:
            return self.config[axis]['data'][0]
        elif self._synchronization in HARDWARE_SYNC:
            return self.config[axis]['data']

    def _get_values(self, image_nr):
        self._log.debug('GetValues method: reading image %d', image_nr)
        for channel, axes in self.started.items():
            while True:
                try:
                    if self.aborted:
                        return
                    data = self.xspress3.ReadScalers([image_nr, channel])
                    break
                except:
                    time.sleep(0.1)
            for axis in axes:
                index = self.config[axis]['index']
                self.config[axis]['data'].append(data[index])

    def _clean_acquisition(self):
        if self.last_image_read == -1:
            return

        self.last_image_read = -1
        self.last_image_ready = -1
        self.repetitions = 0
        self.start = False
        self.abort = False
        self.started = {}
        self._clean_data()

    def _clean_data(self):
        for axis_config in self.config.values():
            axis_config['data'] = []

    def GetAxisExtraPar(self, axis, name):
        name = name.lower()
        if name == 'channel':
            return self.config[axis]['channel']
        elif name == 'scalar_index':
            return self.config[axis]['index']

    def SetAxisExtraPar(self, axis, name, value):
        name = name.lower()
        if name == 'channel':
            if value > self.nr_channels:
                raise ValueError(f'Channel number must be less than '
                                 f'{self.nr_channels}')
            self.config[axis]['channel'] = value
        elif name == 'scalar_index':
            self.config[axis]['index'] = value
