#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char executable[PATH_MAX], resolved[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(executable);

    if (_NSGetExecutablePath(executable, &size) != 0 ||
        !realpath(executable, resolved)) {
        fputs("Could not locate the Aider Deck bundle.\n", stderr);
        return 1;
    }

    *strrchr(resolved, '/') = '\0';
    int length = snprintf(script, sizeof(script), "%s/../Resources/aider-deck.sh", resolved);
    if (length < 0 || (size_t)length >= sizeof(script)) {
        fputs("The Aider Deck bundle path is too long.\n", stderr);
        return 1;
    }

    char **args = calloc((size_t)argc + 2, sizeof(*args));
    if (!args) {
        perror("Could not prepare the Aider Deck launcher");
        return 1;
    }
    args[0] = "/bin/bash";
    args[1] = script;
    int next = 2;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) != 0)
            args[next++] = argv[i];
    }
    execv(args[0], args);
    perror("Could not start the Aider Deck launcher");
    free(args);
    return 1;
}
