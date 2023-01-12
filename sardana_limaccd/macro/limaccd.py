import copy
import pprint
import os

from sardana.macroserver.macro import Macro, Type, Optional

LIMA_ENV = '_LimaConfiguration'


def get_env(macro_obj):
    try:
        conf = copy.deepcopy(macro_obj.getEnv(LIMA_ENV))
    except Exception:
        raise ValueError('There is not lima detector defined. '
                         'See def_lima_conf')
    return conf


# class reconfig_lima(Macro):
#     """
#     Macro to be sure that the configuration of the lima is correct
#     """
#     def run(self):
#         pass


class set_lima_conf(Macro):
    """
    Macro to set parameter of the lima channel configuration
    "xpress3_image": {
      "prefix": "XP3_",
      "suffix": ".h5",
      "index": "05d",
      "scan_sub_dir": "scan_{ScanID:04d}",
      "directory": "{ScanDir}/xp3"

    """
    param_def = [
        ['lima_channel', Type.TwoDExpChannel, None, ''],
        ['parameter', Type.String, None, ''],
        ['value', Type.String, None, '']
    ]

    def run(self, lima_channel, parameter, value):
        conf = get_env(self)

        alias = lima_channel.alias()
        if alias not in conf:
            raise ValueError('The detector is not on the configuration. '
                             'Use def_lima_conf macro to include it.')

        conf[alias][parameter] = value
        self.setEnv(LIMA_ENV, conf)


class get_lima_conf(Macro):
    """
    Macro to get lima channel's configuration
    "xpress3_image": {
      "prefix": "XP3_",
      "suffix": ".h5",
      "index": "05d",
      "scan_sub_dir": "scan_{ScanID:04d}",
      "directory": "{ScanDir}/xp3"

    """
    param_def = [
        ['lima_channel', Type.TwoDExpChannel, None, ''],
        ['parameter', Type.String, Optional, '']
    ]
    result_def = [['value', Type.String, None, 'Parameter value']]

    def run(self, lima_channel, parameter):
        conf = get_env(self)

        alias = lima_channel.alias()

        if alias not in conf:
            raise ValueError('The detector is not on the configuration')

        if parameter is not None:
            try:
                return conf[alias][parameter]
            except KeyError:
                ValueError('Wrong parameter')
        else:
            self.output('{}'.format(alias))
            self.output(pprint.pformat(conf[alias], width=10))


class def_lima_conf(Macro):
    """
    Macro to define a new lima configuration by default it uses:
        prefix: "<lima_channel>",
        suffix: ".edf",
        index: "04d",
        scan_sub_dir: "scan_{ScanID:04d}",
        directory: "{ScanDir}/<lima_channel>"
    """
    param_def = [['lima_channel', Type.TwoDExpChannel, None, ''],
                 ['parameters', [
                  ['parameter', Type.String, None, 'Parameter to set'],
                  ['value', Type.String, None, 'Value to set'],
                  {'min': 0}], None, 'List of parameters']]

    def run(self, lima_channel, parameters):
        try:
            conf = get_env(self)
        except Exception:
            conf = {}
        alias = lima_channel.alias()
        if alias in conf:
            raise ValueError('The lima channel is defined in the '
                             'configuration. See set_lima_conf/get_lima_conf')

        conf[alias] = {"prefix": "{}".format(lima_channel),
                       "suffix": ".edf",
                       "index": "04d",
                       "scan_sub_dir": "scan_{ScanID:04d}",
                       "directory": "{{ScanDir}}/{}".format(lima_channel)}

        conf[alias].update(dict(parameters))
        self.setEnv(LIMA_ENV, conf)
        self.output('Add %s lima configuration', alias)


class udef_lima_conf(Macro):
    """
    Macro to remove a lima configuration for a lima_channel:
        prefix: "<lima_channel>",
        suffix: ".edf",
        index: "04d",
        scan_sub_dir: "scan_{ScanID:04d}",
        directory: {ScanDir}/<lima_channel>
    """
    param_def = [['lima_channel', Type.TwoDExpChannel, None, '']]

    def run(self, lima_channel):
        try:
            conf = get_env(self)
        except Exception:
            conf = {}

        alias = lima_channel.alias()
        if alias not in conf:
            raise ValueError('The lima channel is not defined in the '
                             'configuration.')

        conf.pop(alias)
        self.setEnv(LIMA_ENV, conf)
        self.output('Removed %s lima configuration', alias)


class lima_hook(Macro):
    """
    Macro to configure ValueRefPattern of the Lima channel according to  the
    lima configuration environment:
    <directory>/<scan_sub_dir>/<prefix>{index:<index>}<suffix>
    """

    def run(self):
        conf = get_env(self)
        scan_dir = self.getEnv('ScanDir')
        scan_id = self.getEnv('ScanID')
        mg_active = self.getEnv('ActiveMntGrp')
        mg = self.getMeasurementGroup(mg_active)

        for element in mg.getChannelLabels():
            if element not in conf:
                continue
            # Prepare the Value Reference Pattern for the element
            # directory may contain <ScanDir>
            lima_dir = conf[element]['directory'].format(ScanDir=scan_dir)
            # scan_sub_dir may contain <ScanID>
            lima_sub_dir = conf[element]['scan_sub_dir'].format(ScanID=scan_id)
            image_folder = os.path.join(lima_dir, lima_sub_dir)
            if not os.path.exists(image_folder):
                os.makedirs(image_folder)
            index = conf[element]['index']
            prefix = conf[element]['prefix']
            suffix = conf[element]['suffix'].split('.')[1]
            # TODO Find a nice way
            image_patter = "file://{}/{}".format(image_folder, prefix)
            image_patter += "{"
            image_patter += "index:{}".format(index)
            image_patter += "}"
            image_patter += ".{}".format(suffix)
            self.info('Configured %s channel according to the lima '
                      'configuration: %s', element, image_patter)
            # TODO Use the MntGrp API
            self.set_meas_conf('ValueRefPattern', image_patter, element, mg)
            self.set_meas_conf('ValueRefEnabled', True, element, mg)
