from __future__ import division
from pyrpl.modules import SoftwareModule, SignalLauncher
from pyrpl.attributes import SelectProperty, BoolProperty, StringProperty
from .model import Model
from .signals import *
from pyrpl.widgets.module_widgets import LockboxWidget
from pyrpl.pyrpl_utils import get_unique_name_list_from_class_list, all_subclasses
from .sequence import Sequence

from collections import OrderedDict
from PyQt4 import QtCore


def all_classnames():
    from .models import *
    return OrderedDict([(subclass.__name__, subclass) for subclass in
                                 [Lockbox] + all_subclasses(Lockbox)])


class ClassnameProperty(SelectProperty):
    """
    Lots of lockbox attributes need to be updated when model is changed
    """
    def set_value(self, obj, val):
        super(ClassnameProperty, self).set_value(obj, val)
        obj._classname_changed()
        return val


class SignalLauncherLockbox(SignalLauncher):
    """
    A SignalLauncher for the lockbox
    """
    output_created = QtCore.pyqtSignal(list)
    output_deleted = QtCore.pyqtSignal(list)
    output_renamed = QtCore.pyqtSignal()
    stage_created = QtCore.pyqtSignal(list)
    stage_deleted = QtCore.pyqtSignal(list)
    stage_renamed = QtCore.pyqtSignal()
    model_changed = QtCore.pyqtSignal()
    state_changed = QtCore.pyqtSignal()
    add_input = QtCore.pyqtSignal(list)
    input_calibrated = QtCore.pyqtSignal(list)
    remove_input = QtCore.pyqtSignal(list)
    update_transfer_function = QtCore.pyqtSignal(list)

    def __init__(self, module):
        super(SignalLauncherLockbox, self).__init__(module)
        self.timer_lock = QtCore.QTimer()
        self.timer_lock.timeout.connect(self.module.goto_next)
        self.timer_lock.setSingleShot(True)

    def kill_timers(self):
        """
        kill all timers
        """
        self.timer_lock.stop()

    # state_changed = QtCore.pyqtSignal() # need to change the color of buttons in the widget
    # state is now a standard Property, signals are caught by the update_attribute_by_name function of the widget.


class Lockbox(SoftwareModule):
    """
    A Module that allows to perform feedback on systems that are well described by a physical model.
    """
    _section_name = 'lockbox'
    _widget_class = LockboxWidget
    _setup_attributes = ["classname", "default_sweep_output", "auto_relock"]
    _gui_attributes = _setup_attributes
    _signal_launcher = SignalLauncherLockbox

    classname = ClassnameProperty(options=[]) #all_models().keys())
    default_sweep_output = SelectProperty(options=[])
    auto_relock = BoolProperty()

    parameter_name = "parameter"
    # possible units to describe the physical parameter to control e.g. ['m', 'MHz']
    units = ['V']
    # list of input signals that can be implemented
    input_cls = [InputFromOutput]

    def _init_module(self):
        # make inputs
        inputnames = get_unique_name_list_from_class_list(self.input_cls)
        self.inputs = []
        for name, cls in zip(inputnames, self.input_cls):
            input = cls(self, name)
            self._add_input(input)
            input.load_setup_attributes()
            input.setup()
        # outputs will be updated later
        self.outputs = []
        # show all available models and set current one
        self.__class__.classname.change_options(self, sorted(all_classnames().keys()))
        self.classname = self.__class__.__name__
        # load availbale sequences
        self._sequence = Sequence(self, 'sequence')
        # initial state is unlocked
        self.state = "unlock"
        # parameters are updated and outputs are loaded by load_setup_attributes (called at the end of __init__)
        #### outputs are only slightly affected by a change of model: only the unit of their DC-gain might become
        #### obsolete, in which case, it needs to be changed to some value...
        #for output in self.outputs:
        #    output.update_for_model()

    @property
    def asg(self):
        if not hasattr('_asg') or self._asg is None:
            self._asg = self.pyrpl.asgs.pop(self.name)
        return self._asg

    def sweep(self):
        """
        Performs a sweep of one of the output. No output default kwds to avoid
        problems when use as a slot.
        """
        self.unlock()
        for output in self.outputs:
            output.reset_ival()
        index = self._output_names.index(self.default_sweep_output)
        output = self.outputs[index]
        output.sweep()
        self.state = "sweep"

    def goto_next(self):
        """
        Goes to the stage immediately after the current one
        """
        if self.state=='sweep' or self.state=='unlock':
            index = 0
        else:
            index = self._stage_names.index(self.state) + 1
        stage = self._stage_names[index]
        self.goto(stage)
        self._signal_launcher.timer_lock.setInterval(self._get_stage(stage).duration * 1000)
        if index + 1 < len(self._sequence.stages):
            self._signal_launcher.timer_lock.start()

    def goto(self, stage_name):
        """
        Sets up the lockbox to the stage named stage_name
        """
        self._get_stage(stage_name).setup()

    def lock(self):
        """
        Launches the full lock sequence, stage by stage until the end.
        """
        self.unlock()
        self.goto_next()

    def unlock(self):
        """
        Unlocks all outputs, without touching the integrator value.
        """
        self.state = 'unlock'
        self._signal_launcher.timer_lock.stop()
        for output in self.outputs:
            output.unlock()

    def calibrate_all(self):
        """
        Calibrates successively all inputs
        """
        for input in self.inputs:
            input.calibrate()

    def _setup(self):
        """
        Sets up the lockbox
        """
        for output in self.outputs:
            output._setup()

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, val):
        if not val in ['unlock', 'sweep'] + [stage.name for stage in self._sequence.stages]:
            raise ValueError("State should be either unlock, or a valid stage name")
        self._state = val
        # To avoid explicit reference to gui here, one could consider using a DynamicSelectAttribute...
        self._signal_launcher.state_changed.emit()
        return val

    @property
    def _stage_names(self):
        return self._sequence.stage_names

    @property
    def _output_names(self):
        return [output.name for output in self.outputs]

    def _get_unique_output_name(self):
        idx = 1
        name = 'output' + str(idx)
        while (name in self._output_names):
            idx += 1
            name = 'output' + str(idx)
        return name

    def _add_output(self):
        """
        Outputs of the lockbox are added dynamically (for now, inputs are defined by the model).
        """
        output = self._add_output_no_save()
        output.name = output.name  # trigers the write in the config file
        return output

    def _add_output_no_save(self):
        """
        Adds and returns and output without touching the config file (useful
        when loading an output from the config file)
        """
        if self.pyrpl.pids.n_available() < 1:
            raise ValueError(
                "All pids are currently in use. Cannot create any more "
                "outputs.")
        output = OutputSignal(self)
        # doesn't trigger write in the config file
        output._name = self._get_unique_output_name()
        self.outputs.append(output)
        setattr(self, output.name, output)
        self._sequence.update_outputs()
        self.__class__.default_sweep_output.\
            change_options(self, [out.name for out in self.outputs])
        """
        if self.widget is not None:
            # Since adding/removing outputs corresponds to dynamic creation of
            # Modules, our attribute-based way of
            # hiding gui update is not effective. Since this is a highly
            # exceptional situation, I don't find it too bad.
            self.widget.add_output(output)
        """
        self._signal_launcher.output_created.emit([output])
        return output

    def _remove_output(self, output, allow_remove_last=False):
        """
        Removes and clear output from the list of outputs. if allow_remove_last is left to False, an exception is raised
        when trying to remove the last output.
        """
        if isinstance(output, basestring):
            output = self._get_output(output)
        if not allow_remove_last:
            if len(self.outputs)<=1:
                raise ValueError("There has to be at least one output.")
        if hasattr(self, output.name):
            delattr(self, output.name)
        output.clear()
        self.outputs.remove(output)
        self._sequence.update_outputs()
        if 'outputs' in self.c._keys():
            if output.name in self.c.outputs._keys():
                self.c.outputs._pop(output.name)
        self.__class__.default_sweep_output.change_options(self,
                                                           [output_var.name for
                                                            output_var in
                                                            self.outputs])
        self._signal_launcher.output_deleted.emit([output])

    def _remove_all_outputs(self):
        """
        Removes all outputs, even the last one.
        """
        while(len(self.outputs)>0):
            self._remove_output(self.outputs[-1], allow_remove_last=True)

    def _rename_output(self, output, new_name):
        """
        This changes the name of the output in many different places: lockbox attribute, config file, pid's owner
        """
        if new_name in self._output_names and self._get_output(new_name)!=output:
            raise ValueError("Name %s already exists for an output"%new_name)
        if hasattr(self, output.name):
            delattr(self, output.name)
        setattr(self, new_name, output)
        if output._autosave_active:
            output.c._rename(new_name)
        output._name = new_name
        if output.pid is not None:
            output.pid.owner = new_name
        self._sequence.update_outputs()
        self.__class__.default_sweep_output.change_options(self, [out.name for out in self.outputs])
        self._signal_launcher.output_renamed.emit()

    def _add_stage(self):
        """
        adds a stage to the lockbox sequence
        """
        return self._sequence.add_stage()

    def _remove_stage(self, stage):
        """
        Removes stage from the lockbox seequence
        """
        self._sequence.remove_stage(stage)

    def _rename_stage(self, stage, new_name):
        self._sequence.rename_stage(stage, new_name)

    def _remove_all_stages(self):
        self._sequence.remove_all_stages()

    def load_setup_attributes(self):
        """
        This function needs to be overwritten to retrieve the child module
        attributes as well
        """
        self._remove_all_outputs()
        # load outputs, prevent saving wrong default_sweep_output at startup
        self._autosave_active = False
        if self.c is not None:
            if 'outputs' in self.c._dict.keys():
                for name, output in self.c.outputs._dict.items():
                    if name != 'states':
                        output = self._add_output_no_save()
                        output._autosave_active = False
                        self._rename_output(output, name)
                        output.load_setup_attributes()
                        output._autosave_active = True
        if len(self.outputs)==0:
            self._add_output()  # add at least one output
        self._autosave_active = True # activate autosave

        # load inputs
        for input in self.inputs:
            input._autosave_active = False
            input.load_setup_attributes()
            input._autosave_active = True

        # load normal attributes (model, default_sweep_output)
        super(Lockbox, self).load_setup_attributes()

        # load sequence
        self._sequence._autosave_active = False
        self._sequence.load_setup_attributes()
        self._sequence._autosave_active = True

    def _remove_input(self, input):
        input.clear()
        self.inputs.remove(input)
        self._signal_launcher.remove_input.emit([input])

    def _add_input(self, input):
        self.inputs.append(input)
        setattr(self, input.name, input)
        self._signal_launcher.add_input.emit([input])

    def _get_input(self, name):
        """
        retrieves an input by name
        """
        return self.inputs[[input.name for input in self.inputs].index(name)]

    def _get_output(self, name):
        """
        retrieves an output by name
        """
        return self.outputs[[output.name for output in self.outputs].index(name)]

    def _get_stage(self, name):
        """
        retieves a stage by name
        """
        return self._sequence.get_stage(name)

    def _classname_changed(self):
        # check whether a new object must be instantiated and return if not
        if self.classname == self.__class__.__name__:
            return
        # delete former lockbox (free its resources)
        self._delete_Lockbox()
        # make a new object
        new_lockbox = self._make_Lockbox(self.parent, self.name)
        # update references
        self.parent.lockbox = new_lockbox
        self.parent.software_modules[self.parent.software_modules.index(self)] = new_lockbox
        # launch signal
        new_lockbox._signal_launcher.model_changed.emit()

    def _delete_Lockbox(self):
        self._signal_launcher.kill_timers()
        for o in self.outputs:
            o.unsetup()
        for i in self.inputs:
            i.unsetup()

    @classmethod
    def _make_Lockbox(cls, parent, name):
        # identify class name
        try:
            classname = parent.c.lockboxs.lockbox.classname
        except:
            classname = cls.__name__
        # return instance of the class
        return all_classnames()[classname](parent, name)
