.text
.balign 4
.globl calloc_2_0
.type calloc_2_0, %function
.symver calloc_2_0, calloc@@GLIBC_2.0, remove
calloc_2_0: .long 0
.balign 4
.globl __libc_memalign_2_0
.type __libc_memalign_2_0, %function
.symver __libc_memalign_2_0, __libc_memalign@@GLIBC_2.0, remove
__libc_memalign_2_0: .long 0
.balign 4
.globl malloc_2_0
.type malloc_2_0, %function
.symver malloc_2_0, malloc@@GLIBC_2.0, remove
malloc_2_0: .long 0
.balign 4
.globl ___tls_get_addr_2_3
.type ___tls_get_addr_2_3, %function
.symver ___tls_get_addr_2_3, ___tls_get_addr@@GLIBC_2.3, remove
___tls_get_addr_2_3: .long 0
.balign 4
.globl free_2_0
.type free_2_0, %function
.symver free_2_0, free@@GLIBC_2.0, remove
free_2_0: .long 0
.balign 4
.globl _dl_mcount_2_1
.type _dl_mcount_2_1, %function
.symver _dl_mcount_2_1, _dl_mcount@@GLIBC_2.1, remove
_dl_mcount_2_1: .long 0
.balign 4
.globl realloc_2_0
.type realloc_2_0, %function
.symver realloc_2_0, realloc@@GLIBC_2.0, remove
realloc_2_0: .long 0
.balign 4
.globl __tls_get_addr_2_3
.type __tls_get_addr_2_3, %function
.symver __tls_get_addr_2_3, __tls_get_addr@@GLIBC_2.3, remove
__tls_get_addr_2_3: .long 0
.rodata
.data
.balign 4
.globl __libc_stack_end_2_1
.type __libc_stack_end_2_1, %object
.size __libc_stack_end_2_1, 4
.symver __libc_stack_end_2_1, __libc_stack_end@@GLIBC_2.1, remove
__libc_stack_end_2_1: .fill 4, 1, 0
.balign 4
.globl _r_debug_2_0
.type _r_debug_2_0, %object
.size _r_debug_2_0, 20
.symver _r_debug_2_0, _r_debug@@GLIBC_2.0, remove
_r_debug_2_0: .fill 20, 1, 0
