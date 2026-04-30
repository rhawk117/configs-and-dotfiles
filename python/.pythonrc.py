"""
~/.pythonrc  —  Python REPL startup file
Set PYTHONSTARTUP=~/.pythonrc (or wherever you put this)
"""

import atexit
import dataclasses as dc
import inspect
from importlib import reload
import json
import os
import rlcompleter
import shutil
import sys
import textwrap
from pathlib import Path
from pprint import pprint
from typing import Any




def _esc(*codes: int) -> str:
    """Return a readline-safe ANSI escape sequence."""
    return f'\001\033[{";".join(str(c) for c in codes)}m\002'


RESET = _esc(0)
BOLD = _esc(1)
DIM = _esc(2)

RED, GREEN, YELLOW = _esc(31), _esc(32), _esc(33)
BLUE, MAGENTA, CYAN = _esc(34), _esc(35), _esc(36)
BRED, BGREEN, BYELLOW = _esc(91), _esc(92), _esc(93)
BBLUE, BMAGENTA, BCYAN = _esc(94), _esc(95), _esc(96)


def fg256(n: int) -> str:
    """256-color foreground: fg256(208) -> orange."""
    return _esc(38, 5, n)


def fgrgb(r: int, g: int, b: int) -> str:
    """Truecolor foreground."""
    return f'\001\033[38;2;{r};{g};{b}m\002'

@dc.dataclass(slots=True)
class _ShellGlobals:
    """Shell-style utilities injected into the REPL namespace."""

    def _print_tree(
        self,
        directory: Path,
        *,
        current_depth: int,
        max_depth: int,
        prefix: str = '',
    ) -> None:
        if current_depth > max_depth:
            return
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except PermissionError:
            print(f'{prefix}    [permission denied]')
            return

        for i, item in enumerate(entries):
            last = i == len(entries) - 1
            branch = '└── ' if last else '├── '
            print(f'{prefix}{branch}{item.name}{"/" if item.is_dir() else ""}')
            if item.is_dir():
                self._print_tree(
                    item,
                    current_depth=current_depth + 1,
                    max_depth=max_depth,
                    prefix=prefix + ('    ' if last else '│   '),
                )

    def tree(self, path: str | Path = '.', *, depth: int = 2) -> None:
        """
        Print a directory tree.

        Parameters
        ----------
        path : str | Path, optional
            Root to start from, by default '.'
        depth : int, optional
            Maximum traversal depth, by default 2
        """
        if not (0 < depth < sys.getrecursionlimit()):
            raise ValueError(f'depth must be between 1 and {sys.getrecursionlimit() - 1}')
        root = Path(path).expanduser().resolve()
        print(f'{root}/')
        self._print_tree(root, current_depth=1, max_depth=depth)

    def clear(self) -> None:
        """Clear the terminal screen."""
        os.system('cls' if os.name == 'nt' else 'clear')

    def cd(self, path: Path | str) -> Path:
        """
        Change working directory.

        Returns the resolved destination path.
        """
        dest = Path(path).expanduser().resolve()
        os.chdir(dest)
        return dest

    def ls(self, path: str | Path = '.') -> list[Path]:
        """
        List directory contents (dirs first, then files, alphabetical).

        Returns the sorted list of Path objects.
        """
        directory = Path(path).expanduser().resolve()
        items = sorted(
            directory.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.lower()),
        )
        for item in items:
            print(f'{item.name}{"/" if item.is_dir() else ""}')
        return items

    def up(self, n: int = 1) -> Path:
        """
        Go up N directory levels (default 1).

        Returns the new working directory.
        """
        if n < 1:
            raise ValueError('n must be >= 1')
        dest = Path.cwd().joinpath(*('..' for _ in range(n))).resolve()
        os.chdir(dest)
        return dest

    def cat(self, file: str | Path, *, n: bool = False) -> str:
        """
        Read and print a file's contents as UTF-8 text.

        Parameters
        ----------
        file : str | Path
        n : bool, optional
            Show line numbers, by default False
        """
        path = Path(file).expanduser().resolve()
        text = path.read_text('utf-8')
        if n:
            lines = text.splitlines()
            width = len(str(len(lines)))
            for i, line in enumerate(lines, 1):
                print(f'{i:{width}} | {line}')
        else:
            print(text)
        return text

    @property
    def global_mapping(self) -> dict[str, Any]:
        return {
            'pwd': Path.cwd,
            'home': Path.home,
            'ls': self.ls,
            'cd': self.cd,
            'up': self.up,
            'cat': self.cat,
            'clear': self.clear,
            'tree': self.tree,
            'Path': Path,
            'os': os,
        }




@dc.dataclass(slots=True)
class _ConsoleSetup:
    pp_func: Any = dc.field(default=pprint)

    def try_install_rich(self) -> None:
        """Install Rich pretty-printing and traceback hooks if available."""
        try:
            from rich import inspect
            from rich.pretty import install as _rich_pretty_install
            from rich.traceback import install as _rich_tb_install

            _rich_pretty_install()
            _rich_tb_install(show_locals=False)
            self.pp_func = inspect
        except ImportError:
            print('[pythonrc] rich not found -- using pprint', file=sys.stderr)

    def configure_python_314_colors(self) -> None:
        """Apply a custom syntax-highlight theme (Python 3.14+ only)."""
        if sys.version_info < (3, 14):
            return
        try:
            from _colorize import ANSIColors, default_theme, set_theme

            custom_theme = default_theme.copy_with(
                syntax=default_theme.syntax.copy_with(
                    keyword=ANSIColors.BOLD_YELLOW,
                    string=ANSIColors.INTENSE_BLUE,
                    number=ANSIColors.INTENSE_CYAN,
                    comment=ANSIColors.GREY,
                    builtin=ANSIColors.INTENSE_MAGENTA,
                )
            )
            set_theme(custom_theme)
        except ImportError:
            pass

    def configure_prompts(self) -> None:
        """Set colored sys.ps1 / sys.ps2 (only defined in interactive mode)."""
        if not hasattr(sys, 'ps1'):
            return
        sys.ps1 = f'{BOLD}{BGREEN}py>{RESET} '
        sys.ps2 = f'{BOLD}{YELLOW} | {RESET}'




def _import_readline():
    """
    Return a readline-compatible module or None.

    Import order:
      1. stdlib readline  (Linux, macOS)
      2. pyreadline3      (Windows -- pip install pyreadline3)
      3. None             (graceful no-op)
    """
    try:
        import readline

        return readline
    except ImportError:
        pass
    try:
        import pyreadline3 as readline

        return readline
    except ImportError:
        return None


def _setup_history_and_completion(namespace: dict[str, Any]) -> None:
    """
    Configure readline history (persistent) and tab completion.

    History file
    ------------
    Respects the PYTHON_HISTORY env var (introduced natively in 3.13).
    Falls back to ~/.python_history. Saved automatically on exit via atexit.

    Completion
    ----------
    Uses rlcompleter wired to the live injected namespace so custom commands
    (ls, cd, tree, ...) appear in completions immediately.

    macOS / libedit
    ---------------
    macOS ships libedit instead of GNU readline. The tab-binding syntax
    differs; we detect and use the correct form automatically.

    Python 3.13+ PyREPL
    -------------------
    The new PyREPL manages its own history/completion independently.
    This setup still runs -- it configures the underlying readline layer
    that PyREPL delegates to, so history and completion continue to work.
    Set PYTHON_BASIC_REPL=1 to force the old REPL if needed.
    """
    rl = _import_readline()
    if rl is None:
        print(
            '[pythonrc] readline not available -- history and completion disabled',
            file=sys.stderr,
        )
        return

    history_path = Path(
        os.environ.get('PYTHON_HISTORY') or (Path.home() / '.python_history')
    )

    try:
        rl.read_history_file(history_path)
    except FileNotFoundError, OSError:
        pass  # First run -- file will be created on exit

    rl.set_history_length(5_000)
    atexit.register(rl.write_history_file, history_path)

    completer = rlcompleter.Completer(namespace)
    rl.set_completer(completer.complete)

    rl.set_completer_delims(' \t\n`~!@#$%^&*()-=+[{]}\\|;:\'",<>?/')

    is_libedit = 'libedit' in (getattr(rl, '__doc__', '') or '')
    if is_libedit:
        rl.parse_and_bind('bind ^I rl_complete')
    else:
        rl.parse_and_bind('tab: complete')




def _make_show_commands(global_mapping: dict[str, Any]):
    col_w = shutil.get_terminal_size((80, 24)).columns
    divider = '-' * min(col_w, 60)

    def show_commands() -> None:
        """Print all commands/names available in this REPL session."""
        print(f'\n{divider}')
        print('  Available in this session')
        print(divider)

        with_docs: dict[str, tuple[str, str]] = {}
        bare: list[str] = []

        for name, obj in sorted(global_mapping.items()):
            doc = inspect.getdoc(obj)
            if callable(obj) and doc:
                try:
                    sig = str(inspect.signature(obj))
                except ValueError, TypeError:
                    sig = '(...)'
                with_docs[name] = (sig, doc.splitlines()[0])
            else:
                bare.append(name)

        for name, (sig, first_line) in with_docs.items():
            print(f'  {BOLD}{BGREEN}{name}{RESET}{DIM}{sig}{RESET}')
            print(f'      {first_line}')

        if bare:
            names = ', '.join(f'{BCYAN}{n}{RESET}' for n in bare)
            print(f'\n  Pre-imported: {names}')

        print(f'{divider}\n')

    return show_commands



def _initialize() -> None:
    console = _ConsoleSetup()
    console.try_install_rich()
    console.configure_python_314_colors()
    console.configure_prompts()

    shell = _ShellGlobals()

    extra: dict[str, Any] = {
        **shell.global_mapping,
        'pp': console.pp_func,
        'json': json,
        'reload': reload,
        'pprint': pprint,
        'which': shutil.which,
    }
    extra['show_commands'] = _make_show_commands(extra)

    banner = textwrap.dedent(f"""\

    {'=' * 50}
      Python REPL (extended)   made by: rhawk117

      Version:    {sys.version.split()[0]}
      Executable: {sys.executable}

      Type show_commands() to see what's available
    {'=' * 50}
    """)
    print(banner)

    globals().update(extra)
    _setup_history_and_completion(globals())


_initialize()
