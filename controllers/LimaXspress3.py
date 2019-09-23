#!/usr/bin/env python

from LimaCoTiCtrl import LimaCoTiCtrl
from sardana.pool import AcqSynch
import PyTango
import time

class LimaXspress3CTCtrl(LimaCoTiCtrl):
    """
    TODO:
    """

    gender = "LimaCounterTimer"
    model = "Basic"
    organization = "CELLS - ALBA"
    image = "Lima_ctrl.png"
    logo = "ALBA_logo.png"

    MaxDevice = 11

    def __init__(self, inst, props, *args, **kwargs):
        LimaCoTiCtrl.__init__(self, inst, props, *args, **kwargs)
        self._log.debug("__init__(%s, %s): Entering...", repr(inst),
                        repr(props))

        plugins = self._limaccd['plugin_list'].value
        if 'xspress3' not in plugins:
            raise RuntimeError('The Lima DS is not compatible with Xspress3')
        idx = plugins.index('xspress3')
        xspress3_name = plugins[idx + 1]
        self._xspress3 = PyTango.DeviceProxy(xspress3_name)

        self._nr_channels = self._xspress3.read_attribute('numChan').value
        self.MaxDevice = (self._nr_channels * 2) + 1
        self._last_dt_read = -1
        self._start_channels = []

    def _get_values(self, image_nr):
        # TODO optimize reading
        self._log.debug('GetValues method: reading image %d' % image_nr)
        for channel in self._start_channels:
            data = self._xspress3.ReadScalers([image_nr, channel])
            dt = (channel + 1) * 2
            dtf = dt + 1
            # dt value
            if dt in self._data_buff:
                self._data_buff[dt] += [data[9]]
            # dtf value
            if dtf in self._data_buff:
                self._data_buff[dtf] += [data[10]]

    def _clean_acquisition(self):
        LimaCoTiCtrl._clean_acquisition(self)
        self._last_dt_read = self._last_image_read
        self._start_channels = []

    def _clean_data(self):
        for channel in range(self._nr_channels):
            dt = (channel + 1) * 2
            dtf = dt + 1
            if dt in self._data_buff:
                self._data_buff[dt] = []
            if dtf in self._data_buff:
                self._data_buff[dtf] = []

    def AddDevice(self, axis):
        if axis == 1:
            LimaCoTiCtrl.AddDevice(self, axis)
        else:
            self._data_buff[axis] = []

    def PreStartOne(self, axis, value):
        self._log.debug('Start axis %s' % axis)
        if axis == 1:
            pass
        else:
            chn = int(axis/2) - 1
            if chn > self._nr_channels:
                return False 
            if chn not in self._start_channels:
                 self._start_channels.append(chn)
        return True

    def ReadAll(self):
        LimaCoTiCtrl.ReadAll(self)
        self._clean_data()
        if not self._new_data:
            return
        if self._synchronization == AcqSynch.SoftwareTrigger:
            self._get_values(0)
        elif self._synchronization == AcqSynch.HardwareTrigger:
            self._last_dt_read += 1
            nr_images = self._last_image_read - self._last_dt_read + 1
            for i in range(nr_images):
                image_nr = self._last_dt_read + i
                self._get_values(image_nr)
            self._last_dt_read = self._last_image_read

    def ReadOne(self, axis):
        if axis == 1:
            return LimaCoTiCtrl.ReadOne(self, axis)
        else:
            if self._synchronization == AcqSynch.SoftwareTrigger:
                return self._data_buff[axis][0]
            elif self._synchronization == AcqSynch.HardwareTrigger:
                return self._data_buff[axis]
