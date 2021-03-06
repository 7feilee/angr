import logging

from .sim_state import SimState
from .calling_conventions import DEFAULT_CC, SimRegArg, SimStackArg, PointerWrapper
from .callable import Callable
from .errors import AngrAssemblyError


l = logging.getLogger("angr.factory")


_deprecation_cache = set()
def deprecate(name, replacement):
    def wrapper(func):
        def inner(*args, **kwargs):
            if name not in _deprecation_cache:
                l.warning("factory.%s is deprecated! Please use factory.%s instead.", name, replacement)
                _deprecation_cache.add(name)
            return func(*args, **kwargs)
        return inner
    return wrapper


class AngrObjectFactory(object):
    """
    This factory provides access to important analysis elements.
    """
    def __init__(self, project):
        self.project = project
        self._default_cc = DEFAULT_CC[project.arch.name]

    @property
    def default_engine(self):
        return self.project.engines.default_engine

    @property
    def procedure_engine(self):
        return self.project.engines.procedure_engine

    def snippet(self, addr, jumpkind=None, **block_opts):
        if self.project.is_hooked(addr) and jumpkind != 'Ijk_NoHook':
            hook = self.project._sim_procedures[addr]
            size = hook.kwargs.get('length', 0)
            return HookNode(addr, size, self.project.hooked_by(addr))
        else:
            return self.block(addr, **block_opts).codenode # pylint: disable=no-member

    def successors(self, *args, **kwargs):
        """
        Perform execution using any applicable engine. Enumerate the current engines and use the
        first one that works. Return a SimSuccessors object classifying the results of the run.

        :param state:           The state to analyze
        :param addr:            optional, an address to execute at instead of the state's ip
        :param jumpkind:        optional, the jumpkind of the previous exit
        :param inline:          This is an inline execution. Do not bother copying the state.

        Additional keyword arguments will be passed directly into each engine's process method.
        """

        return self.project.engines.successors(*args, **kwargs)

    def blank_state(self, **kwargs):
        """
        Returns a mostly-uninitialized state object. All parameters are optional.

        :param addr:            The address the state should start at instead of the entry point.
        :param initial_prefix:  If this is provided, all symbolic registers will hold symbolic values with names
                                prefixed by this string.
        :param fs:              A dictionary of file names with associated preset SimFile objects.
        :param concrete_fs:     bool describing whether the host filesystem should be consulted when opening files.
        :param chroot:          A path to use as a fake root directory, Behaves similarly to a real chroot. Used only
                                when concrete_fs is set to True.
        :param kwargs:          Any additional keyword args will be passed to the SimState constructor.
        :return:                The blank state.
        :rtype:                 SimState
        """
        return self.project.simos.state_blank(**kwargs)

    def entry_state(self, **kwargs):
        """
        Returns a state object representing the program at its entry point. All parameters are optional.

        :param addr:            The address the state should start at instead of the entry point.
        :param initial_prefix:  If this is provided, all symbolic registers will hold symbolic values with names
                                prefixed by this string.
        :param fs:              a dictionary of file names with associated preset SimFile objects.
        :param concrete_fs:     boolean describing whether the host filesystem should be consulted when opening files.
        :param chroot:          a path to use as a fake root directory, behaves similar to a real chroot. used only when
                                concrete_fs is set to True.
        :param argc:            a custom value to use for the program's argc. May be either an int or a bitvector. If
                                not provided, defaults to the length of args.
        :param args:            a list of values to use as the program's argv. May be mixed strings and bitvectors.
        :param env:             a dictionary to use as the environment for the program. Both keys and values may be
                                mixed strings and bitvectors.
        :return:                The entry state.
        :rtype:                 SimState
        """

        return self.project.simos.state_entry(**kwargs)

    def full_init_state(self, **kwargs):
        """
        Very much like :meth:`entry_state()`, except that instead of starting execution at the program entry point,
        execution begins at a special SimProcedure that plays the role of the dynamic loader, calling each of the
        initializer functions that should be called before execution reaches the entry point.

        :param addr:            The address the state should start at instead of the entry point.
        :param initial_prefix:  If this is provided, all symbolic registers will hold symbolic values with names
                                prefixed by this string.
        :param fs:              a dictionary of file names with associated preset SimFile objects.
        :param concrete_fs:     boolean describing whether the host filesystem should be consulted when opening files.
        :param chroot:          a path to use as a fake root directory, behaves similar to a real chroot. used only when
                                concrete_fs is set to True.
        :param argc:            a custom value to use for the program's argc. May be either an int or a bitvector. If
                                not provided, defaults to the length of args.
        :param args:            a list of values to use as arguments to the program. May be mixed strings and bitvectors.
        :param env:             a dictionary to use as the environment for the program. Both keys and values may be
                                mixed strings and bitvectors.
        :return:                The fully initialized state.
        :rtype:                 SimState
        """
        return self.project.simos.state_full_init(**kwargs)

    def call_state(self, addr, *args, **kwargs):
        """
        Returns a state object initialized to the start of a given function, as if it were called with given parameters.

        :param addr:            The address the state should start at instead of the entry point.
        :param args:            Any additional positional arguments will be used as arguments to the function call.

        The following parametrs are optional.

        :param base_state:      Use this SimState as the base for the new state instead of a blank state.
        :param cc:              Optionally provide a SimCC object to use a specific calling convention.
        :param ret_addr:        Use this address as the function's return target.
        :param stack_base:      An optional pointer to use as the top of the stack, circa the function entry point
        :param alloc_base:      An optional pointer to use as the place to put excess argument data
        :param grow_like_stack: When allocating data at alloc_base, whether to allocate at decreasing addresses
        :param toc:             The address of the table of contents for ppc64
        :param initial_prefix:  If this is provided, all symbolic registers will hold symbolic values with names
                                prefixed by this string.
        :param fs:              A dictionary of file names with associated preset SimFile objects.
        :param concrete_fs:     bool describing whether the host filesystem should be consulted when opening files.
        :param chroot:          A path to use as a fake root directory, Behaves similarly to a real chroot. Used only
                                when concrete_fs is set to True.
        :param kwargs:          Any additional keyword args will be passed to the SimState constructor.
        :return:                The state at the beginning of the function.
        :rtype:                 SimState

        The idea here is that you can provide almost any kind of python type in `args` and it'll be translated to a
        binary format to be placed into simulated memory. Lists (representing arrays) must be entirely elements of the
        same type and size, while tuples (representing structs) can be elements of any type and size.
        If you'd like there to be a pointer to a given value, wrap the value in a `SimCC.PointerWrapper`. Any value
        that can't fit in a register will be automatically put in a
        PointerWrapper.

        If stack_base is not provided, the current stack pointer will be used, and it will be updated.
        If alloc_base is not provided, the current stack pointer will be used, and it will be updated.
        You might not like the results if you provide stack_base but not alloc_base.

        grow_like_stack controls the behavior of allocating data at alloc_base. When data from args needs to be wrapped
        in a pointer, the pointer needs to point somewhere, so that data is dumped into memory at alloc_base. If you
        set alloc_base to point to somewhere other than the stack, set grow_like_stack to False so that sequencial
        allocations happen at increasing addresses.
        """
        return self.project.simos.state_call(addr, *args, **kwargs)

    def simgr(self, thing=None, **kwargs):
        return self.simulation_manager(thing=thing, **kwargs)

    def simulation_manager(self, thing=None, **kwargs):
        """
        Constructs a new simulation manager.

        :param thing:           Optional - What to put in the new SimulationManager's active stash (either a SimState or a list of SimStates).
        :param kwargs:          Any additional keyword arguments will be passed to the SimulationManager constructor
        :returns:               The new SimulationManager
        :rtype:                 angr.sim_manager.SimulationManager

        Many different types can be passed to this method:

        * If nothing is passed in, the SimulationManager is seeded with a state initialized for the program
          entry point, i.e. :meth:`entry_state()`.
        * If a :class:`SimState` is passed in, the SimulationManager is seeded with that state.
        * If a list is passed in, the list must contain only SimStates and the whole list will be used to seed the SimulationManager.
        """
        if thing is None:
            thing = [ self.entry_state() ]
        elif isinstance(thing, (list, tuple)):
            if any(not isinstance(val, SimState) for val in thing):
                raise AngrError("Bad type to initialize SimulationManager")
        elif isinstance(thing, SimState):
            thing = [ thing ]
        else:
            raise AngrError("BadType to initialze SimulationManager: %s" % repr(thing))

        return SimulationManager(self.project, active_states=thing, **kwargs)

    def callable(self, addr, concrete_only=False, perform_merge=True, base_state=None, toc=None, cc=None):
        """
        A Callable is a representation of a function in the binary that can be interacted with like a native python
        function.

        :param addr:            The address of the function to use
        :param concrete_only:   Throw an exception if the execution splits into multiple states
        :param perform_merge:   Merge all result states into one at the end (only relevant if concrete_only=False)
        :param base_state:      The state from which to do these runs
        :param toc:             The address of the table of contents for ppc64
        :param cc:              The SimCC to use for a calling convention
        :returns:               A Callable object that can be used as a interface for executing guest code like a
                                python function.
        :rtype:                 angr.surveyors.caller.Callable
        """
        return Callable(self.project,
                        addr=addr,
                        concrete_only=concrete_only,
                        perform_merge=perform_merge,
                        base_state=base_state,
                        toc=toc,
                        cc=cc)


    def cc(self, args=None, ret_val=None, sp_delta=None, func_ty=None):
        """
        Return a SimCC (calling convention) parametrized for this project and, optionally, a given function.

        :param args:        A list of argument storage locations, as SimFunctionArguments.
        :param ret_val:     The return value storage location, as a SimFunctionArgument.
        :param sp_delta:    Does this even matter??
        :param func_ty:     The protoype for the given function, as a SimType.

        Relevant subclasses of SimFunctionArgument are SimRegArg and SimStackArg, and shortcuts to them can be found on
        this `cc` object.

        For stack arguments, offsets are relative to the stack pointer on function entry.
        """
        return self._default_cc(arch=self.project.arch,
                                  args=args,
                                  ret_val=ret_val,
                                  sp_delta=sp_delta,
                                  func_ty=func_ty)

    def cc_from_arg_kinds(self, fp_args, ret_fp=None, sizes=None, sp_delta=None, func_ty=None):
        """
        Get a SimCC (calling convention) that will extract floating-point/integral args correctly.

        :param arch:        The Archinfo arch for this CC
        :param fp_args:     A list, with one entry for each argument the function can take. True if the argument is fp,
                            false if it is integral.
        :param ret_fp:      True if the return value for the function is fp.
        :param sizes:       Optional: A list, with one entry for each argument the function can take. Each entry is the
                            size of the corresponding argument in bytes.
        :param sp_delta:    The amount the stack pointer changes over the course of this function - CURRENTLY UNUSED
        :parmm func_ty:     A SimType for the function itself
        """
        return self._default_cc.from_arg_kinds(arch=self.project.arch,
                fp_args=fp_args,
                ret_fp=ret_fp,
                sizes=sizes,
                sp_delta=sp_delta,
                func_ty=func_ty)

    def block(self, addr, size=None, max_size=None, byte_string=None, vex=None, thumb=False, backup_state=None,
              opt_level=None, num_inst=None, traceflags=0,
              insn_bytes=None, insn_text=None,  # backward compatibility
              strict_block_end=None, collect_data_refs=False,
              ):

        if insn_bytes is not None and insn_text is not None:
            raise AngrError("You cannot provide both 'insn_bytes' and 'insn_text'!")

        if insn_bytes is not None:
            byte_string = insn_bytes

        if insn_text is not None:
            byte_string = self.project.arch.asm(insn_text, addr=addr, as_bytes=True, thumb=thumb)
            if byte_string is None:
                # assembly failed
                raise AngrAssemblyError("Assembling failed. Please make sure keystone is installed, and the assembly"
                                        " string is correct.")

        if max_size is not None:
            l.warning('Keyword argument "max_size" has been deprecated for block(). Please use "size" instead.')
            size = max_size
        return Block(addr, project=self.project, size=size, byte_string=byte_string, vex=vex, thumb=thumb,
                     backup_state=backup_state, opt_level=opt_level, num_inst=num_inst, traceflags=traceflags,
                     strict_block_end=strict_block_end, collect_data_refs=collect_data_refs,
                     )

    def fresh_block(self, addr, size, backup_state=None):
        return Block(addr, project=self.project, size=size, backup_state=backup_state)

    cc.SimRegArg = SimRegArg
    cc.SimStackArg = SimStackArg
    _default_cc = None
    callable.PointerWrapper = PointerWrapper
    call_state.PointerWrapper = PointerWrapper


    #
    # Private methods
    #

    @deprecate('sim_run()', 'successors()')
    def sim_run(self, *args, **kwargs):
        return self.successors(*args, **kwargs)

    @deprecate('sim_block()', 'successors(default_engine=True)')
    def sim_block(self, *args, **kwargs):
        kwargs['default_engine'] = True
        return self.successors(*args, **kwargs)

    #
    # Compatibility layer
    #

    @deprecate('path_group()', 'simulation_manager()')
    def path_group(self, thing=None, **kwargs):
        return self.simgr(thing, **kwargs)

    @deprecate('path()', 'entry_state()')
    def path(self, state=None, **kwargs):
        if state is not None:
            return state
        return self.entry_state(**kwargs)


from .errors import AngrError
from .sim_manager import SimulationManager
from .codenode import HookNode
from .block import Block
