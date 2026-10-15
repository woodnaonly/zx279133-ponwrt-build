import sys, os, logging
import tftpy

root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'staging')
port = int(sys.argv[2]) if len(sys.argv) > 2 else 6969
logging.basicConfig(level=logging.INFO, format='%(message)s')
print('TFTP root: %s  port: %d' % (root, port), flush=True)
srv = tftpy.TftpServer(root)
try:
    srv.listen('0.0.0.0', port)
except KeyboardInterrupt:
    print('stopped')
