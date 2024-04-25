import copy
import pprint
import os
import re

from sardana.macroserver.macro import Macro, Type, Optional

LIMA_ENV = '_LimaConfiguration'


def get_env(macro_obj, var=LIMA_ENV):
    try:
        conf = copy.deepcopy(macro_obj.getEnv(var))
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
      "directory": "{ScanDir}/xp3",
      "create_folders": True

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
        if parameter == 'create_folders':
            value = eval(value)
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
      "directory": "{ScanDir}/xp3",
      "create_folders": True

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
        directory: "{ScanDir}/<lima_channel>",
        create_folders: True
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
                       "directory": "{{ScanDir}}/{}".format(lima_channel),
                       "create_folders": True}

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
        create_folders: True
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


def find_dynamic_variables(string):
    variables_brackets = re.findall(r'\{.*?\}', string)
    variables = [var[1:-1] for var in variables_brackets]
    return variables


def str_formatting_env_variables(macro_obj, string):
    """
    Function to translate from environment variables to string recursively.
    It takes a string with keywords that are going to be replaced by their
    corresponding environment variable value. Hence, the keywords must be
    environment variables with value strings or numbers.
    Everything between curly brackets '{}' will be considered a keyword.
    eg:
    string: '{env1}/test/01'
    env1: '/data'
    result: '/data/test/01'
    """
    if string == '""' or string == "''":
        return ""
    dynamic_variables = find_dynamic_variables(string)
    # Get only the name in case it also specifies number formatting
    dynamic_variables = [var.split(":")[0] for var in dynamic_variables]
    # Validate that all variables exist as environment variables
    current_env = get_env(macro_obj, var=None)
    invalid_variables = []
    for var in dynamic_variables:
        if var not in current_env:
            invalid_variables.append(var)
    if invalid_variables:
        raise ValueError("Dynamic variables '{}' do not exist in environment"
                         "".format(invalid_variables))

    environment_variables = {var : current_env[var] for var in
                             dynamic_variables}
    formatted_str = string.format(**environment_variables)

    if find_dynamic_variables(formatted_str):
        # Recursive step
        return str_formatting_env_variables(macro_obj, formatted_str)

    return formatted_str

def create_image_folder(macro_obj, channel_conf):
    # Prepare the Value Reference Pattern for the element
    # directory may contain any environment variable
    lima_dir = str_formatting_env_variables(
        macro_obj, channel_conf['directory'])
    # scan_sub_dir may contain any environment variable
    lima_sub_dir = str_formatting_env_variables(
        macro_obj, channel_conf['scan_sub_dir'])
    image_folder = os.path.join(lima_dir, lima_sub_dir)
    # Check if the creation of the folders is needed, if the key
    # does not exist the hook will create the folder by default.
    create_folders = channel_conf.get('create_folders', True)
    if create_folders and not os.path.exists(image_folder):
        os.makedirs(image_folder)
    return image_folder


class lima_hook(Macro):
    """
    Macro to configure ValueRefPattern of the Lima channel according to  the
    lima configuration environment:
    <directory>/<scan_sub_dir>/<prefix>{index:<index>}<suffix>
    """

    def run(self):
        conf = get_env(self)
        mg_active = self.getEnv('ActiveMntGrp')
        mg = self.getMeasurementGroup(mg_active)

        for channel in mg.getChannelLabels():
            if channel not in conf:
                continue

            channel_conf = conf[channel]
            image_folder = create_image_folder(self, channel_conf)
            index = channel_conf['index']
            prefix = str_formatting_env_variables(
                self, channel_conf['prefix'])
            suffix = channel_conf['suffix'].split('.')[1]
            # TODO Find a nice way
            image_path = "file://"+image_folder
            image_name = "{}".format(prefix)
            image_name += "{"
            image_name += "index:{}".format(index)
            image_name += "}"
            image_name += ".{}".format(suffix)
            image_pattern = os.path.join(image_path, image_name)
            self.info('Configured %s channel according to the lima '
                      'configuration: %s', channel, image_pattern)
            # TODO Use the MntGrp API
            self.set_meas_conf('ValueRefPattern', image_pattern, channel, mg)
            self.set_meas_conf('ValueRefEnabled', True, channel, mg)
