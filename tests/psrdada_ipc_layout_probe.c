/* Read-only ABI evidence for the host agent's SysV resource ownership checks. */
#include <stdio.h>
#include <sys/ipc.h>
#include <ipcbuf.h>

int main(void)
{
    printf("{\"readers\":%u,\"transfers\":%u,\"sync_bytes\":%zu,\"key_bytes\":%zu}\n",
           (unsigned) IPCBUF_READERS, (unsigned) IPCBUF_XFERS,
           sizeof(ipcsync_t), sizeof(key_t));
    return 0;
}
