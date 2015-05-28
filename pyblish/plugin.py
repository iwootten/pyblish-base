"""Plug-in system

Works similar to how OSs look for executables; i.e. a number of
absolute paths are searched for a given match. The predicate for
executables is whether or not an extension matches a number of
options, such as ".exe" or ".bat".

In this system, the predicate is whether or not a fname starts
with "validate" and ends with ".py"

Attributes:
    patterns: Regular expressions used for lookup of plugins.

"""

# Standard library
import os
import re
import sys
import logging
import inspect

# Local library
import pyblish
import pyblish.lib
import pyblish.error
import pyblish.legacy

from .vendor import yaml
from .vendor import iscompatible


log = logging.getLogger("pyblish.plugin")


class Provider(object):
    """Dependency provider"""

    def __init__(self):
        self._services = dict()

    @property
    def services(self):
        s = pyblish._registered_services.copy()
        s.update(self._services)
        return s

    @classmethod
    def args(cls, func):
        return [a for a in inspect.getargspec(func)[0]
                if a not in ("self", "cls")]

    def invoke(self, func):
        """Supply function `func` with objects to its signature

        Raises:
            KeyError if an argument asked for is not available

        Returns:
            Result of `func`

        """

        services = self.services

        if not all(arg in services.keys() for arg in self.args(func)):
            raise KeyError("Unavailable service requested.")

        inject = dict((k, v) for k, v in self.services.items()
                      if k in self.args(func))

        return func(**inject)

    def inject(self, name, obj):
        self._services[name] = obj


class Config(dict):
    """Wrapper for Pyblish configuration file.

    .. note:: Config is a singleton.

    Attributes:
        DEFAULTCONFIG: Name of default configuration file
        PACKAGEDIR: Absolute path to parent package of Config
        DEFAULTCONFIGPATH: Absolute path to default configuration file

        default: Access to default configuration

    Usage:
        >>> config = Config()
        >>> for key, value in config.iteritems():
        ...     assert key in config
        >>> assert Config() is Config()

    """

    _instance = None

    PACKAGEDIR = os.path.dirname(__file__)
    DEFAULTCONFIG = "config.yaml"
    DEFAULTCONFIGPATH = os.path.join(PACKAGEDIR, DEFAULTCONFIG)

    log = logging.getLogger("pyblish.Config")

    def __new__(cls, *args, **kwargs):
        """Make Config into a singleton"""
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(
                cls, *args, **kwargs)
            cls._instance.reset()
        return cls._instance

    def reset(self):
        """Remove all configuration and re-read from disk"""
        self.clear()
        self.load()

    def load(self):
        """Load default configuration from package dir"""
        path = self.DEFAULTCONFIGPATH
        data = {"DEFAULTCONFIG": self.DEFAULTCONFIG,
                "DEFAULTCONFIGPATH": self.DEFAULTCONFIGPATH}

        with open(path, "r") as f:
            loaded_data = yaml.load(f)

        if data is not None:
            loaded_data.update(data)

        for key, value in loaded_data.iteritems():
            self[key] = value

        return True


@pyblish.lib.log
class Plugin(object):
    """Abstract base-class for plugins

    Attributes:
        hosts: Mandatory specifier for which host application
            this plug-in is compatible with.
        families: Supported families.
        version: Mandatory version for forwards-compatibility.
            Pyblish is (currently not) using the version to allow
            for plug-ins incompatible with a particular running
            instance of Pyblish to co-exist alongside compatible
            versions.
        order: Order in which this plug-in is processed. This is
            used internally to control which plug-ins are processed
            before another so as to allow plug-ins to communicate
            with each other. E.g. one plug-in may provide critical
            information to another and so must be allowed to be
            processed first.
        optional: Whether or not plug-in can be skipped by the user.
        requires: Which version of Pyblish is required by this plug-in.
            Plug-ins requiring a version newer than the current version
            will not be loaded. 1.0.8 was when :attr:`Plugin.requires`
            was first introduced.
    """

    hosts = list("*")    # Hosts compatible with plugin
    families = list("*")    # Hosts compatible with plugin
    version = (0, 0, 0)  # Current version of plugin
    order = None
    optional = False
    requires = "pyblish>=1"

    def __str__(self):
        return type(self).__name__

    def __repr__(self):
        return u"%s.%s(%r)" % (__name__, type(self).__name__, self.__str__())

    @pyblish.lib.classproperty
    def id(cls):
        return cls.__name__

    def process(self):
        """Primary processing method

        This method is called whenever your plug-in is invoked
        and is injected with object relative to it's signature.

        E.g. process(self, context, instance) will have the current
        context and instance injected into it at run-time.

        Available objects:
            - context
            - instance
            - user
            - time

        Raises:
            Any error

        """

        pass

    def repair(self):
        pass


class Selector(Plugin):
    """Parse a given working scene for available Instances"""

    order = 0


class Validator(Plugin):
    """Validate/check/test individual instance for correctness."""

    order = 1


class Extractor(Plugin):
    """Physically separate Instance from Host into corresponding resources"""

    order = 2


class Conformer(Plugin):
    """Integrates publishes into a pipeline"""

    order = 3


# Aliases
Collector = Selector
Integrator = Conformer


def process(plugin, context, instance=None):
    """Determine whether the given plug-in to be dependency injected"""
    if (hasattr(plugin, "process_instance")
            or hasattr(plugin, "process_context")):
        return pyblish.legacy.process_1_0(plugin, context, instance)
    else:
        return _process(plugin, context, instance)


def repair(plugin, context, instance=None):
    """Determine whether the given plug-in to be dependency injected"""
    if (hasattr(plugin, "repair_instance")
            or hasattr(plugin, "repair_context")):
        return pyblish.legacy.repair_1_0(plugin, context, instance)
    else:
        return _repair(plugin, context, instance)


def _process(plugin, context, instance=None):
    """Produce a single result from a Plug-in

    Returns:
        Result dictionary

    """

    import time

    if "results" not in context.data():
        context.set_data("results", list())

    result = {
        "success": False,
        "plugin": plugin,
        "instance": instance,
        "error": None,
        "records": list(),
        "duration": None
    }

    plugin = plugin()

    provider = pyblish.plugin.Provider()
    provider.inject("context", context)
    provider.inject("instance", instance)

    records = list()
    handler = pyblish.lib.MessageHandler(records)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    __start = time.time()

    try:
        provider.invoke(plugin.process)
        result["success"] = True
    except Exception as error:
        pyblish.lib.extract_traceback(error)
        result["error"] = error

    __end = time.time()

    for record in records:
        result["records"].append(record)

    # Restore balance to the world
    root_logger.removeHandler(handler)

    result["duration"] = (__end - __start) * 1000  # ms

    context.data("results").append(result)

    return result


def _repair(plugin, context, instance=None):
    import time

    if "results" not in context.data():
        context.set_data("results", list())

    result = {
        "success": False,
        "plugin": plugin,
        "instance": instance,
        "error": None,
        "records": list(),
        "duration": None
    }

    plugin = plugin()

    provider = pyblish.plugin.Provider()
    provider.inject("context", context)
    provider.inject("instance", instance)

    records = list()
    handler = pyblish.lib.MessageHandler(records)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    __start = time.time()

    try:
        provider.invoke(plugin.repair)
        result["success"] = True
    except Exception as error:
        pyblish.lib.extract_traceback(error)
        result["error"] = error

    __end = time.time()

    for record in records:
        result["records"].append(record)

    # Restore balance to the world
    root_logger.removeHandler(handler)

    result["duration"] = (__end - __start) * 1000  # ms

    context.data("results").append(result)

    return result


class AbstractEntity(list):
    """Superclass for Context and Instance"""

    def __init__(self):
        self._data = dict()

    def add(self, other):
        """Add member to self

        This is to mimic the interface of set()

        """

        if other not in self:
            return self.append(other)

    def remove(self, other):
        """Remove member from self

        This is to mimic the interface of set()

        """

        index = self.index(other)
        return self.pop(index)

    def data(self, key=None, default=None):
        """Return data from `key`

        Arguments:
            key (str): Name of data to return
            default (object): Optional, value returned if `name`
                does not exist

        """

        if key is None:
            return self._data.copy()

        return self._data.get(key, default)

    def set_data(self, key, value):
        """Modify/insert data into entity

        Arguments:
            key (str): Name of data to add
            value (object): Value of data to add

        """

        self._data[key] = value

    def remove_data(self, key):
        """Remove data from entity

        Arguments;
            key (str): Name of data to remove

        """

        self._data.pop(key)

    def has_data(self, key):
        """Check if entity has key

        Arguments:
            key (str): Key to check

        Return:
            True if it exists, False otherwise

        """

        return key in self._data


class Context(AbstractEntity):
    """Maintain a collection of Instances"""

    def create_instance(self, name):
        """Convenience method of the following.

        >>> ctx = Context()
        >>> inst = Instance("name", parent=ctx)
        >>> ctx.add(inst)

        Example:
            >>> ctx = Context()
            >>> inst = ctx.create_instance(name="Name")

        """

        instance = Instance(name, parent=self)
        return instance


@pyblish.lib.log
class Instance(AbstractEntity):
    """An in-memory representation of one or more files

    Examples include rigs, models.

    Arguments:
        name (str): Name of instance, typically used during
            extraction as name of resulting files.
        parent (AbstractEntity): Optional parent. This is
            supplied automatically when creating instances with
            :class:`Context.create_instance()`.

    Attributes:
        name (str): Name of instance, used in plugins
        parent (AbstractEntity): Optional parent of instance

    """

    def __eq__(self, other):
        return self.name == getattr(other, "name", None)

    def __ne__(self, other):
        return self.name != getattr(other, "name", None)

    def __repr__(self):
        return u"%s.%s(\"%s\")" % (__name__, type(self).__name__, self)

    def __str__(self):
        return self.name

    @property
    def id(self):
        return self.name

    def __init__(self, name, parent=None):
        super(Instance, self).__init__()
        assert isinstance(name, basestring)
        assert parent is None or isinstance(parent, AbstractEntity)
        self.name = name
        self.parent = parent

        if parent is not None:
            parent.add(self)

    @property
    def context(self):
        """Return top-level parent; the context"""
        parent = self.parent
        while parent:
            try:
                parent = parent.parent
            except:
                break
        assert isinstance(parent, Context)
        return parent

    def data(self, key=None, default=None):
        """Treat `name` data-member as an override to native property

        If name is a data-member, it will be used wherever a name is requested.
        That way, names may be overridden via data.

        Example:
            >>> inst = Instance(name="test")
            >>> assert inst.data("name") == "test"
            >>> inst.set_data("name", "newname")
            >>> assert inst.data("name") == "newname"

        """

        value = super(Instance, self).data(key, default)

        if key == "name" and value is None:
            return self.name

        return value


def current_host():
    """Return currently active host

    When running Pyblish from within a host, this function determines
    which host is running and returns the equivalent keyword.

    Example:
        >> # Running within Autodesk Maya
        >> current_host()
        "maya"
        >> # Running within Sidefx Houdini
        >> current_host()
        "houdini"

    """

    executable = os.path.basename(sys.executable).lower()

    if "python" in executable:
        # Running from standalone Python
        return "python"

    if "maya" in executable:
        # Maya is distinguished by looking at the currently running
        # executable of the Python interpreter. It will be something
        # like: "maya.exe" or "mayapy.exe"; without suffix for
        # posix platforms.
        return "maya"

    if "nuke" in executable:
        # Nuke typically includes a version number, e.g. Nuke8.0.exe
        # and mixed-case letters.
        return "nuke"

    # ..note:: The following are guesses, feel free to correct

    if "modo" in executable:
        return "modo"

    if "houdini" in executable:
        return "houdini"

    raise ValueError("Could not determine host from \"%s\"" % executable)


def register_plugin(plugin):
    """Register a new plug-in

    Arguments:
        plugin (Plugin): Plug-in to register

    Raises:
        TypeError if `plugin` is not callable

    """

    if not hasattr(plugin, "__call__"):
        raise TypeError("Plug-in must be callable "
                        "returning an instance of a class")

    pyblish._registered_plugins[plugin.__name__] = plugin


def deregister_plugin(plugin):
    """De-register an existing plug-in

    Arguments:
        plugin (Plugin): Existing plug-in to de-register

    """

    pyblish._registered_plugins.pop(plugin.__name__)


def deregister_all_plugins():
    """De-register all plug-ins"""
    pyblish._registered_plugins.clear()


def register_service(name, obj):
    """Register a new service

    Arguments:
        name (str): Name of service
        obj (object): Any object

    """

    pyblish._registered_services[name] = obj


def deregister_service(name):
    """De-register an existing service by name

    Arguments:
        name (str): Name of service

    """

    pyblish._registered_services.pop(name)


def deregister_all_services():
    """De-register all existing services"""
    pyblish._registered_services.clear()


def registered_services():
    """Return the currently registered services as a dictionary

    .. note:: This returns a copy of the registered paths
        and can therefore not be modified directly.

    """

    return pyblish._registered_services.copy()


def register_plugin_path(path):
    """Plug-ins are looked up at run-time from directories registered here

    To register a new directory, run this command along with the absolute
    path to where you"re plug-ins are located.

    Example:
        >>> import os
        >>> my_plugins = "/server/plugins"
        >>> register_plugin_path(my_plugins)
        '/server/plugins'

    Returns:
        Actual path added, including any post-processing

    """

    if path in pyblish._registered_paths:
        return log.warning("Path already registered: {0}".format(path))

    pyblish._registered_paths.append(path)

    return path


def deregister_plugin_path(path):
    """Remove a pyblish._registered_paths path

    Raises:
        KeyError if `path` isn't registered

    """

    pyblish._registered_paths.remove(path)


def deregister_all_paths():
    """Mainly used in tests"""
    pyblish._registered_paths[:] = []


def registered_paths():
    """Return paths added via registration

    ..note:: This returns a copy of the registered paths
        and can therefore not be modified directly.

    """

    return list(pyblish._registered_paths)


def registered_plugins():
    """Return plug-ins added via :func:`register_plugin`

    .. note:: This returns a copy of the registered plug-ins
        and can therefore not be modified directly

    """

    return pyblish._registered_plugins.values()


def configured_paths():
    """Return paths added via configuration"""
    paths = list()
    config = Config()

    for path_template in config["paths"]:
        variables = {"pyblish": pyblish.lib.main_package_path()}

        plugin_path = path_template.format(**variables)

        paths.append(plugin_path)

    return paths


def environment_paths():
    """Return paths added via environment variable"""

    paths = list()
    config = Config()

    env_var = config["paths_environment_variable"]
    env_val = os.environ.get(env_var)
    if env_val:
        env_paths = env_val.split(os.pathsep)
        for path in env_paths:
            paths.append(path)

        log.debug("Paths from environment: %s" % env_paths)

    return paths


def plugin_paths():
    """Collect paths from all sources.

    This function looks at the three potential sources of paths
    and returns a list with all of them together.

    The sources are:

    - Registered paths using :func:`register_plugin_path`,
    - Paths from the environment variable `PYBLISHPLUGINPATH`
    - Paths from configuration

    Returns:
        list of paths in which plugins may be locat

    """

    paths = list()

    for path in registered_paths() + configured_paths() + environment_paths():
        if path in paths:
            continue
        paths.append(path)

    return paths


def discover(type=None, regex=None, paths=None):
    """Find and return available plug-ins

    This function looks for files within paths registered via
    :func:`register_plugin_path` and those added to `PYBLISHPLUGINPATH`.

    It determines *type* - :class:`Selector`, :class:`Validator`,
    :class:`Extractor` or :class:`Conform` - based on whether it
    matches it's corresponding regular expression; e.g.
    "$validator_*^" for plug-ins of type Validator.

    Arguments:
        type (str, optional): Only return plugins of specified type
            E.g. validators, extractors. In None is specified, return
            all plugins. Available options are "selectors", validators",
            "extractors", "conformers", "collectors" and "integrators".
        regex (str, optional): Limit results to those matching `regex`.
            Matching is done on classes, as opposed to
            filenames, due to a file possibly hosting
            multiple plugins.
        paths (list, optional): Paths to discover plug-ins from.
            If no paths are provided, all paths are searched.

    """

    config = Config()

    patterns = {"validators": config["validators_regex"],
                "extractors": config["extractors_regex"],
                "selectors": config["selectors_regex"],
                "integrators": config["integrators_regex"],
                "collectors": config["collectors_regex"],
                "conformers": config["conformers_regex"]}

    types = {"validators": Validator,
             "extractors": Extractor,
             "selectors": Selector,
             "integrators": Conformer,
             "collectors": Selector,
             "conformers": Conformer}

    if type is not None and type not in patterns:
        raise ValueError("Type not recognised: %s" % type)

    # Include plug-ins from registration
    discovered_plugins = pyblish._registered_plugins.copy()

    # Include plug-ins from registered paths
    types_to_check = [type] if type is not None else patterns.keys()
    for path in paths or plugin_paths():
        path = os.path.normpath(path)
        if not os.path.isdir(path):
            continue

        for fname in os.listdir(path):
            abspath = os.path.join(path, fname)

            if not any(re.match(patterns[type], fname)
                       for type in types_to_check):
                continue

            if not os.path.isfile(abspath):
                continue

            mod_name, _ = os.path.splitext(fname)
            try:
                sys.path.insert(0, path)
                module = pyblish.lib.import_module(mod_name)
                reload(module)

            except Exception as err:
                log.warning("Skipped: \"%s\" (%s)", mod_name, err)
                continue

            finally:
                # Restore sys.path
                # sys.modules.pop(mod_name)
                sys.path.remove(path)

            for name in dir(module):
                if name.startswith("_"):
                    continue

                obj = getattr(module, name)

                if not inspect.isclass(obj):
                    continue

                if not issubclass(obj, Plugin):
                    continue

                discovered_plugins[obj.__name__] = obj

    # Filter discovered
    plugins = list()
    for name, plugin in discovered_plugins.items():
        if not plugin_is_valid(plugin):
            continue

        if not version_is_compatible(plugin):
            log.warning("Plug-in %s not compatible with "
                        "this version (%s) of Pyblish." % (
                            plugin, pyblish.__version__))
            continue

        if not host_is_compatible(plugin):
            continue

        if plugin in plugins:
            log.debug("Duplicate plugin found: %s", plugin)
            continue

        if not any(issubclass(plugin, types[type]) for type in types_to_check):
            continue

        if regex is None or re.match(regex, name):
            plugins.append(plugin)

    sort(plugins)  # In-place
    return plugins


def plugin_is_valid(plugin):
    """Determine whether or not plug-in `plugin` is valid

    Arguments:
        plugin (Plugin): Plug-in to assess

    """

    if not inspect.isclass(plugin):
        return False

    if not issubclass(plugin, Plugin):
        return False

    if not isinstance(plugin.requires, basestring):
        log.debug("Plug-in requires must be of type string: %s", plugin)
        return False

    try:
        if (issubclass(plugin, Selector)
                and not getattr(plugin, "hosts")):
            raise Exception(0)
        if (issubclass(plugin, (Validator, Extractor))
                and not getattr(plugin, "families")
                and not getattr(plugin, "hosts")):
            raise Exception(1)

        if (issubclass(plugin, Conformer)
                and not getattr(plugin, "families")):
            raise Exception(2)

    except Exception as e:
        if e.message == 0:
            log.error("%s: Plug-in not valid, missing hosts.", plugin)
        if e.message == 1:
            log.error("%s: Plug-in not valid, missing hosts and families.",
                      plugin)
        if e.message == 2:
            log.error("%s: Plug-in not valid, missing families.", plugin)
        return False

    return True


def version_is_compatible(plugin):
    """Lookup compatibility between plug-in and current version of Pyblish

    Arguments:
        plugin (Plugin): Plug-in to test against

    """

    if not iscompatible.iscompatible(requirements=plugin.requires,
                                     version=pyblish.version_info):
        return False
    return True


def host_is_compatible(plugin):
    """Determine whether plug-in `plugin` is compatible with the current host

    The current host is determined by :func:`current_host`.

    Arguments:
        plugin (Plugin): Plug-in to assess.

    """

    return any(["*" in plugin.hosts, current_host() in plugin.hosts])


def sort(plugins):
    """Sort `plugins` in-place

    Their order is determined by their `order` attribute,
    which defaults to their standard execution order:

        1. Selection
        2. Validation
        3. Extraction
        4. Conform

    *But may be overridden.

    Arguments:
        plugins (list): Plug-ins to sort

    """

    plugins.sort(key=lambda p: p.order)
    return plugins
