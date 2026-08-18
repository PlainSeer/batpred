.text
.balign 4
.globl calloc_2_4
.type calloc_2_4, %function
.symver calloc_2_4, calloc@@GLIBC_2.4, remove
calloc_2_4: .long 0
.balign 4
.globl __libc_memalign_2_4
.type __libc_memalign_2_4, %function
.symver __libc_memalign_2_4, __libc_memalign@@GLIBC_2.4, remove
__libc_memalign_2_4: .long 0
.balign 4
.globl malloc_2_4
.type malloc_2_4, %function
.symver malloc_2_4, malloc@@GLIBC_2.4, remove
malloc_2_4: .long 0
.balign 4
.globl free_2_4
.type free_2_4, %function
.symver free_2_4, free@@GLIBC_2.4, remove
free_2_4: .long 0
.balign 4
.globl _dl_mcount_2_4
.type _dl_mcount_2_4, %function
.symver _dl_mcount_2_4, _dl_mcount@@GLIBC_2.4, remove
_dl_mcount_2_4: .long 0
.balign 4
.globl realloc_2_4
.type realloc_2_4, %function
.symver realloc_2_4, realloc@@GLIBC_2.4, remove
realloc_2_4: .long 0
.balign 4
.globl __tls_get_addr_2_4
.type __tls_get_addr_2_4, %function
.symver __tls_get_addr_2_4, __tls_get_addr@@GLIBC_2.4, remove
__tls_get_addr_2_4: .long 0
.rodata
.data
.balign 4
.globl __libc_stack_end_2_4
.type __libc_stack_end_2_4, %object
.size __libc_stack_end_2_4, 4
.symver __libc_stack_end_2_4, __libc_stack_end@@GLIBC_2.4, remove
__libc_stack_end_2_4: .fill 4, 1, 0
.balign 4
.globl __stack_chk_guard_2_4
.type __stack_chk_guard_2_4, %object
.size __stack_chk_guard_2_4, 4
.symver __stack_chk_guard_2_4, __stack_chk_guard@@GLIBC_2.4, remove
__stack_chk_guard_2_4: .fill 4, 1, 0
.balign 4
.globl _r_debug_2_4
.type _r_debug_2_4, %object
.size _r_debug_2_4, 20
.symver _r_debug_2_4, _r_debug@@GLIBC_2.4, remove
_r_debug_2_4: .fill 20, 1, 0
