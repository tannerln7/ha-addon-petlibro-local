// bootclock.c
#include <stdio.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

int main(void)
{
    struct timeval tv;
    struct timespec mono;
    FILE *f;

    gettimeofday(&tv, NULL);
    clock_gettime(CLOCK_MONOTONIC, &mono);

    f = fopen("/user/data/bootclock.log", "a");
    if (!f)
        return 1;

    fprintf(f,
        "wall=%ld.%06ld mono=%ld.%09ld\n",
        (long)tv.tv_sec,
        (long)tv.tv_usec,
        (long)mono.tv_sec,
        (long)mono.tv_nsec);

    fclose(f);
    sync();
    return 0;
}
