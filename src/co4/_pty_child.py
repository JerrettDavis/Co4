"""Internal POSIX exec trampoline. No provider credentials or model traffic are handled here."""
import fcntl
import os
import sys
import termios

# Popen creates a new session; make the supplied PTY its controlling terminal.
# A helper process avoids preexec_fn, which is unsafe in a multithreaded parent.
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
os.tcsetpgrp(0, os.getpgrp())
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
