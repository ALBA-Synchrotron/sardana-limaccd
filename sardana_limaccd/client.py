import os
import re
import time
import struct
import logging

import numpy
import tango


# TODO: Include the other formats
LIMA_EXT_FORMAT = {
    'EDF': ['.edf'],
    'HDF5': ['.h5', '.hdf5'],
    'CBF': ['.cbf'],
    'TIFF': ['.tiff']
}


class LimaImageFormat(struct.Struct):

    Magic = 0x44544159
    DTypes = ("u1", "u2", "u4", None, "i1", "i2", "i4")
    DTypeSize = (1, 2, 4, None, 1, 2, 4)

    def __init__(self):
        super(LimaImageFormat, self).__init__("<IHHIIHHHHHHHHIIIIIIII")

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
            frame.shape = d2, d1
            frames.append(frame)
        return frames


LIMA_DECODER = LimaImageFormat()


def saving_for_pattern(pattern):
    # TODO: Improve regexp matching
    # Extract saving configuration from the pattern
    try:
        dir_fp = re.findall('\://(.*?)$', pattern)[0]
    except Exception as error:
        raise ValueError('Wrong pattern: {0!r}'.format(error))

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
        raise ValueError('The extension {0!r} is not valid'.format(suffix))

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


class Acquisition(object):
    """Store information about a specific acquisition"""

    def __init__(self, lima, nb_frames, expo_time, latency_time, trigger_mode):
        self.lima = lima
        trigger_mode = trigger_mode.upper()
        self.config = {
            "acq_nb_frames": nb_frames,
            "acq_expo_time": expo_time,
            "latency_time": latency_time,
            "acq_trigger_mode": trigger_mode
        }
        self.stopped = False
        self._acq_next_number = 0
        self._save_next_number = 0
        self._last_saved_number = -1
        self.nb_starts = 0

    def __getitem__(self, name):
        return self.lima[name]

    def is_int_trig(self):
        return self.config["acq_trigger_mode"] == "INTERNAL_TRIGGER"

    def is_int_trig_multi(self):
        return self.config["acq_trigger_mode"] == "INTERNAL_TRIGGER_MULTI"

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
        self.nb_starts += 1
        if self.lima.saving.enabled:
            # buggy: for low exp_time the next number might be after
            # a few frames already saved
            self._save_next_number = self.lima["saving_next_number"]

    def calc_status(self, acq_status, ready_for_next, idx_ready, idx_saved):
        if acq_status not in {"Ready", "Running"}:
            return acq_status
        if self.stopped:
            return "Ready"
        done = idx_saved if self.lima.saving.enabled else idx_ready
        if done < self.config["acq_nb_frames"] - 1:
            acq_status = "Running"
        trig_mode = self.config["acq_trigger_mode"]
        if ready_for_next and self.is_int_trig_multi() and acq_status == "Running":
            if (idx_ready + 1) >= self.nb_starts:
                acq_status = "Ready"
        return acq_status

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
            return []
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


class Saving(object):

    FILE_PATTERN = \
        "{scheme}://{saving_directory}/{saving_prefix}{index}{saving_suffix}"

    def __init__(self, lima):
        self.lima = lima
        self.first_image_nb = 0
        self.enabled = False
        self.pattern = ""
        self.config = {}

    def filename(self, index):
        scheme = "file"
        if self.config["saving_format"] == "HDF5":
            scheme = "h5file"
        index = self.config["saving_index_format"] % index
        return self.FILE_PATTERN.format(
            scheme=scheme, index=index, **self.config
        )

    def prepare(self):
        if not self.enabled:
            self.config = {"saving_mode": "MANUAL"}
            self.lima["saving_mode"] = "MANUAL"
            return
        self.config = config = saving_for_pattern(self.pattern)
        config["saving_mode"] = "AUTO_FRAME"
        config["saving_overwrite_policy"] = "ABORT"
        names, values = zip(*config.items())
        self.lima[names] = values
        if self.first_image_nb != 0:
            self.lima["saving_next_number"] = -1
            time.sleep(0.05) # empirical value?
            self.lima["saving_prefix"] = config["saving_prefix"]
            monotonic = getattr(time, "monotonic", time.time)
            t0 = monotonic()
            saving_next_number = -1
            # After setting the prefix with saving mode = ABORT,
            # the LimaCCDs takes some seconds to update the saving next
            # number, this time depends of the number of files on the folder
            # with the same pattern. For that reason the controller will
            # change the first saving next number on the first start.

            # Allow to set the First Image Number to any value different to
            # 0, default value on LimaCCDs after writing the prefix with
            # saving mode in Abort
            while saving_next_number == -1 and monotonic() - t0 < 2.5:
                saving_next_number = self.lima["saving_next_number"]
                if saving_next_number == 0:
                    self.lima["saving_next_number"] = self.first_image_nb
                time.sleep(0.03)


class Lima(object):
    """LimaCCD Controller helper class"""

    CAPABILITIES = "saving_format", "acq_trigger_mode"

    def __init__(self, device_name, log=None):
        self._log = log if log else logging.getLogger("Lima")
        self._device_name = device_name
        self._device = None
        self._capabilities = None
        self.saving = Saving(self)

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
            device = tango.DeviceProxy(self._device_name)
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

    def acquisition(self, nb_frames, expo_time, latency_time, trigger_mode):
        return Acquisition(
            self, nb_frames, expo_time, latency_time, trigger_mode,
        )

    def read_frames(self, frame_start, frame_end):
        fmt, buff = self("readImageSeq", (frame_start, frame_end))
        assert fmt == "DATA_ARRAY"
        return LIMA_DECODER.decode(buff, n=frame_end - frame_start)

    def get_status(self):
        return self[
            "acq_status",
            "ready_for_next_image",
            "last_image_ready",
            "last_image_saved"
        ]
