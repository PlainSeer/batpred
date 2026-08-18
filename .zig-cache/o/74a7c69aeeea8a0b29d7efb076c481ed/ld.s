.text
.balign 8
.globl calloc_2_17
.type calloc_2_17, %function
.symver calloc_2_17, calloc@@GLIBC_2.17, remove
calloc_2_17: .quad 0
.balign 8
.globl __libc_memalign_2_17
.type __libc_memalign_2_17, %function
.symver __libc_memalign_2_17, __libc_memalign@@GLIBC_2.17, remove
__libc_memalign_2_17: .quad 0
.balign 8
.globl malloc_2_17
.type malloc_2_17, %function
.symver malloc_2_17, malloc@@GLIBC_2.17, remove
malloc_2_17: .quad 0
.balign 8
.globl free_2_17
.type free_2_17, %function
.symver free_2_17, free@@GLIBC_2.17, remove
free_2_17: .quad 0
.balign 8
.globl _dl_mcount_2_17
.type _dl_mcount_2_17, %function
.symver _dl_mcount_2_17, _dl_mcount@@GLIBC_2.17, remove
_dl_mcount_2_17: .quad 0
.balign 8
.globl realloc_2_17
.type realloc_2_17, %function
.symver realloc_2_17, realloc@@GLIBC_2.17, remove
realloc_2_17: .quad 0
.balign 8
.globl __tls_get_addr_2_17
.type __tls_get_addr_2_17, %function
.symver __tls_get_addr_2_17, __tls_get_addr@@GLIBC_2.17, remove
__tls_get_addr_2_17: .quad 0
.rodata
.data
.balign 8
.globl __libc_stack_end_2_17
.type __libc_stack_end_2_17, %object
.size __libc_stack_end_2_17, 8
.symver __libc_stack_end_2_17, __libc_stack_end@@GLIBC_2.17, remove
__libc_stack_end_2_17: .fill 8, 1, 0
.balign 8
.globl __stack_chk_guard_2_17
.type __stack_chk_guard_2_17, %object
.size __stack_chk_guard_2_17, 8
.symver __stack_chk_guard_2_17, __stack_chk_guard@@GLIBC_2.17, remove
__stack_chk_guard_2_17: .fill 8, 1, 0
.balign 8
.globl _r_debug_2_17
.type _r_debug_2_17, %object
.size _r_debug_2_17, 40
.symver _r_debug_2_17, _r_debug@@GLIBC_2.17, remove
_r_debug_2_17: .fill 40, 1, 0
