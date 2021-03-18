import time
import struct
import logging

import numpy
import tango


class LimaImageFormat(struct.Struct):

    Magic = 0x44544159
    DTypes = ("u1", "u2", "u4", None, "i1", "i2", "i4")
    DTypeSize = (1, 2, 4, None, 1, 2, 4)

    def __init__(self):
        super().__init__("<IHHIIHHHHHHHHIIIIIIII")

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


class Acquisition:

    def __init__(self, lima, nb_frames, expo_time, latency, trigger_mode):
        self.lima = lima
        self.config = {
            "acq_nb_frames": nb_frames,
            "acq_expo_time": expo_time,
            "latency_time": latency_time,
            "acq_trigger_mode": trigger_mode.upper()
        }
        self._acq_next_number = 0
        self._save_next_number = 0
        self._last_saved_number = -1
        self.stopped = False

    def __getitem__(self, name):
        return self.lima[name]

    def stop(self):
        self.lima("stopAcq")
        self.stopped = True

    def abort(self):
        self.lima("abortAcq")
        self.stopped = True

    def prepare(self):
        names, values = zip(*self.config.items())
        self.lima[names] = values
        self.lima("prepareAcq")
        if self.lima.saving.enabled:
            while self["last_image_saved"] != -1:
                time.sleep(0.01)

    def start(self):
        self.lima("startAcq")
        if self.lima.saving.enabled:
            # buggy: for low exp_time the next number might be after
            # a few frames already saved
            self._save_next_number = self.lima["saving_next_number"]

    @property
    def status(self):
        status, saved, acquired = self[
            "acq_status", "last_image_saved", "last_image_ready"
        ]
        if status not in {"Ready", "Running"}:
            return status
        ready = saved if self.lima.saving.enabled else acquired
        if ready < self.nb_frames - 1:
            status = "Running"
        return status

    def next_frame(self):
        last = self['last_image_ready']
        n = self._acq_next_number
        if n > last:
            # no frame available yet
            return
        frame = self.lima.read_frames(n, n + 1)[0]
        self._acq_next_number += 1
        return frame

    def next_frames(self):
        last = self['last_image_ready']
        start = self._acq_next_number
        if start > last:
            # no frame available yet
            []
        frames = self.lima.read_frames(start, last + 1)
        self._acq_next_number += len(frames)
        return frames

    def next_ref_frame(self):
        last = self["last_image_saved"]
        n = self._save_next_number + last
        return self.lima.saving.filename(n)

    def next_ref_frames(self):
        last = self["last_image_saved"]
        n = last - self._last_saved_number
        refs = [
            self.lima.saving.filename(self._save_next_number + i)
            for i in range(n)
        ]
        self._save_next_number += n
        self._last_saved_number += n
        return refs

class Saving:

    FILE_PATTERN = \
        "{scheme}://{saving_directory}/{saving_prefix}{index}{suffix}"

    def __init__(self, lima):
        self.lima = lima
        self.first_image_nb = 0
        self.config = {
            "saving_stream_active": True,
            "saving_directory": "",
            "saving_format": "RAW",
            "saving_index_format": "%04d",
            "saving_suffix": "",
            "saving_prefix": "",
            "saving_mode": "MANUAL",
            "saving_overwrite_policy": "ABORT",
        }

    @property
    def enabled(self):
        return self.config["saving_stream_active"] and \
            self.config["saving_directory"]

    def filename(self, index):
        scheme = "file"
        if self.config["saving_format"] == "HDF5":
            scheme = "h5file"
        index = self.config["saving_index_format"] % index
        return self.FILE_PATTERN.format(
            scheme=scheme, index=index, **self.config
        )

    def prepare(self):
        names, values = zip(*self.config.items())
        self.lima[names] = values
        if self.first_image_nb != 0 and self.enabled:
            self.lima["saving_index_next_number"] = -1
            time.sleep(0.05)
            self.lima["saving_prefix"] = self.config["saving_prefix"]
            t0 = time.monotonic()
            saving_next_number = -1
            while saving_next_number == -1 and time.monotonic() - t0 < 2.5:
                saving_next_number = self.lima["saving_next_number"]
                if saving_next_number == 0:
                    self.lima["saving_next_number"] = self.first_image_nb
                time.sleep(0.03)


class Lima:
    """LimaCCD Controller helper class"""

    CAPABILITIES = "saving_format", "acq_trigger_mode"

    def __init__(self, device_name, log=None):
        self._log = log if log else logging.getLogger("Lima")
        self._device_name = device_name
        self._device = None
        self._capabilities = None

    def __call__(self, name, *args):
        return self.device.command_inout(name, *args)

    def __getitem__(self, name):
        if isinstance(name, str):
            return self.device.read_attribute(name).value
        else:
            return [attr.value for attr in self.device.read_attributes(name)]

    def __setitem__(self, name, value):
        if isinstance(name, str):
            self.device.write_attribute(name, value)
        else:
            self.device.write_attributes(tuple(zip(name, value)))

    @property
    def device(self):
        if self._device is None:
            device = tango.DeviceProxy(self.device_name)
            device.reset()
            self._device = device
        return self._device

    @property
    def capabilities(self):
        if self._capabilities is None:
            self._capabilities = {
                cap: self("getAttrStringValueList", cap)
                for cap in self.CAPABILITIES
            }
        return self._capabilities

    def acquisition(self, nb_frames, expo_time):
        return Acquisition(self, nb_frames, expo_time)

    def read_frames(self, frame_start, frame_end):
        fmt, buff = self("readImageSeq", (frame_start, frame_end))
        assert fmt == "DATA_ARRAY"
        return LIMA_DECODER.decode(buff, n=frame_end - frame_start)




