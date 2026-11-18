#import <AppKit/AppKit.h>

@interface DeckLauncher : NSObject <NSApplicationDelegate>
@property (strong) NSTask *task;
@property BOOL quitting;
@end

@implementation DeckLauncher
- (void)applicationDidFinishLaunching:(NSNotification *)notification
{
    (void)notification;
    NSURL *script = [NSBundle.mainBundle URLForResource:@"aider-deck" withExtension:@"sh"];
    if (!script) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Aider Deck could not find its startup script.";
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }
    NSMutableArray<NSString *> *arguments = [NSMutableArray arrayWithObject:script.path];
    NSArray<NSString *> *original = NSProcessInfo.processInfo.arguments;
    for (NSUInteger i = 1; i < original.count; i++) {
        if (![original[i] hasPrefix:@"-psn_"]) {
            [arguments addObject:original[i]];
        }
    }

    self.task = [[NSTask alloc] init];
    self.task.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    self.task.arguments = arguments;
    __weak DeckLauncher *launcher = self;
    self.task.terminationHandler = ^(NSTask *task) {
        (void)task;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (launcher.quitting) {
                [NSApp replyToApplicationShouldTerminate:YES];
            } else {
                [NSApp terminate:nil];
            }
        });
    };
    NSError *error = nil;
    if (![self.task launchAndReturnError:&error]) {
        NSAlert *alert = [NSAlert alertWithError:error];
        [alert runModal];
        [NSApp terminate:nil];
    }
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender
{
    (void)sender;
    if (self.task.running) {
        self.quitting = YES;
        [self.task terminate];
        return NSTerminateLater;
    }
    return NSTerminateNow;
}
@end

int main(void)
{
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        __attribute__((objc_precise_lifetime)) DeckLauncher *launcher = [[DeckLauncher alloc] init];
        app.delegate = launcher;
        NSMenu *menuBar = [[NSMenu alloc] init];
        NSMenuItem *appItem = [[NSMenuItem alloc] init];
        NSMenu *appMenu = [[NSMenu alloc] init];
        [appMenu addItemWithTitle:@"Quit Aider Deck" action:@selector(terminate:) keyEquivalent:@"q"];
        appItem.submenu = appMenu;
        [menuBar addItem:appItem];
        app.mainMenu = menuBar;
        [app run];
    }
    return 0;
}
