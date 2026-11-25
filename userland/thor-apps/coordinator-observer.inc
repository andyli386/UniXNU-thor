typedef id (*CoordinatorFactory)(id, SEL, id, unsigned long long, BOOL, BOOL *,
                                 NSError *__autoreleasing *);
static CoordinatorFactory nativeCoordinatorFactory;
static Class identityClass;
static SEL identityBundleID, threadIdentityInit, threadIdentityResolve;
static bool threadPersonaFallback;
static _Atomic bool sawFactory, coordinatorHandedOut;

static bool rejected_persona(NSError *error)
{
    for (unsigned depth = 0; depth < 4 && [error isKindOfClass:NSError.class]; ++depth) {
        if (([error.domain isEqual:@"IXErrorDomain"] && error.code == 56) ||
            ([error.domain isEqual:@"MIInstallerErrorDomain"] && error.code == 191))
            return true;
        error = error.userInfo[NSUnderlyingErrorKey];
    }
    return false;
}

static const char *unqualified_type(const char *encoding)
{
    while (*encoding && strchr("rnNoORV", *encoding)) ++encoding;
    return encoding;
}

static bool type_matches(const char *encoding, const char *required)
{
    encoding = unqualified_type(encoding);
    while (*required == '^') {
        if (*encoding != '^') return false;
        encoding = unqualified_type(encoding + 1);
        ++required;
    }
    if (strcmp(encoding, required) == 0) return true;
    /* A quoted Objective-C class has the ABI of id. Blocks stay distinct. */
    return strcmp(required, "@") == 0 && encoding[0] == '@' && encoding[1] == '"' &&
           strchr(encoding + 2, '"') != NULL && strchr(encoding + 2, '"')[1] == '\0';
}

static bool matches(Method method, const char *result, unsigned count,
                    const char *const *arguments)
{
    if (!method || method_getNumberOfArguments(method) != count + 2) return false;
    char encoding[48];
    method_getReturnType(method, encoding, sizeof(encoding));
    if (!type_matches(encoding, result)) return false;
    for (unsigned i = 0; i < count; ++i) {
        method_getArgumentType(method, i + 2, encoding, sizeof(encoding));
        if (!type_matches(encoding, arguments[i])) return false;
    }
    return true;
}

static os_unfair_lock activeLock = OS_UNFAIR_LOCK_INIT;
static NSString *activeAppID;

static void select_application(NSString *identifier)
{
    os_unfair_lock_lock(&activeLock);
    activeAppID = identifier;
    if (identifier) {
        atomic_store(&sawFactory, false);
        atomic_store(&coordinatorHandedOut, false);
    }
    os_unfair_lock_unlock(&activeLock);
}

static void mark_coordinator(NSString *identifier, bool handedOut)
{
    os_unfair_lock_lock(&activeLock);
    if (activeAppID == identifier) {
        atomic_store(&sawFactory, true);
        if (handedOut) atomic_store(&coordinatorHandedOut, true);
    }
    os_unfair_lock_unlock(&activeLock);
}

static id observe_coordinator(id self, SEL command, id identity, unsigned long long client,
                                 BOOL create, BOOL *created, NSError *__autoreleasing *error)
{
    os_unfair_lock_lock(&activeLock);
    NSString *identifier = activeAppID;
    os_unfair_lock_unlock(&activeLock);
    if (!identifier || client != 10 || !create || ![identity isKindOfClass:identityClass] ||
        ![((id (*)(id, SEL))objc_msgSend)(identity, identityBundleID) isEqual:identifier]) {
        return nativeCoordinatorFactory(self, command, identity, client, create, created, error);
    }

    mark_coordinator(identifier, false);
    NSError *first = nil;
    BOOL didCreate = NO;
    id coordinator = nativeCoordinatorFactory(self, command, identity, client, create, &didCreate, &first);
    if (created) *created = didCreate;
    if (error) *error = first;
    receipt("PONG APPS: stock coordinator available=%d created=%d\n", coordinator != nil, didCreate);
    log_error("stock coordinator", first);
    if (coordinator || didCreate) mark_coordinator(identifier, true);
    if (coordinator || didCreate || !threadPersonaFallback || !rejected_persona(first))
        return coordinator;

    id threadIdentity = ((id (*)(id, SEL, NSString *))objc_msgSend)(
        [identityClass alloc], threadIdentityInit, identifier);
    if (!threadIdentity) {
        receipt("PONG APPS: native current-thread persona unavailable\n");
        return nil;
    }
    NSError *resolution = nil;
    BOOL valid = ((BOOL (*)(id, SEL, NSError *__autoreleasing *))objc_msgSend)(
        threadIdentity, threadIdentityResolve, &resolution);
    receipt("PONG APPS: native current-thread persona resolved=%d\n", valid);
    log_error("thread persona", resolution);
    if (!valid) return nil;

    NSError *second = nil;
    coordinator = nativeCoordinatorFactory(self, command, threadIdentity, client, create, &didCreate, &second);
    if (created) *created = didCreate;
    if (error) *error = second;
    receipt("PONG APPS: thread coordinator available=%d created=%d\n", coordinator != nil, didCreate);
    log_error("thread coordinator", second);
    if (coordinator || didCreate) mark_coordinator(identifier, true);
    return coordinator;
}

static const char *method_encoding(Method method)
{
    return method ? method_getTypeEncoding(method) ?: "-" : "-";
}

static void install_coordinator_observer(void)
{
    identityClass = objc_lookUpClass("IXApplicationIdentity");
    Class coordinator = objc_lookUpClass("IXInitiatingAppInstallCoordinator");
    SEL select = sel_registerName("processScopedCoordinatorForAppWithIdentity:withClientID:createIfNotExisting:created:error:");
    /* 22H374's stock system-app migrator reads MIAppIdentity.bundleID. */
    identityBundleID = sel_registerName("bundleID");
    threadIdentityInit = sel_registerName("initUsingPersonaFromCurrentThreadAndBundleIdentifier:");
    threadIdentityResolve = sel_registerName("resolvePersonaWithError:");
    Method factory = class_getClassMethod(coordinator, select);
    Method bundleID = class_getInstanceMethod(identityClass, identityBundleID);
    Method initializer = class_getInstanceMethod(identityClass, threadIdentityInit);
    Method resolver = class_getInstanceMethod(identityClass, threadIdentityResolve);
    const char *factoryArgs[] = {"@", "Q", "B", "^B", "^@"};
    const char *identityArgs[] = {"@"};
    const char *errorArgs[] = {"^@"};
    receipt("PONG APPS: native coordinator ABI factory=%.96s bundleID=%.48s thread_init=%.48s resolver=%.48s\n",
            method_encoding(factory), method_encoding(bundleID),
            method_encoding(initializer), method_encoding(resolver));
    if (!matches(factory, "@", 5, factoryArgs) || !matches(bundleID, "@", 0, NULL)) {
        receipt("PONG APPS: coordinator observer unavailable (stock ABI)\n");
        return;
    }
    threadPersonaFallback = matches(initializer, "@", 1, identityArgs) &&
                            matches(resolver, "B", 1, errorArgs);
    nativeCoordinatorFactory = (CoordinatorFactory)method_getImplementation(factory);
    Class meta = object_getClass(coordinator);
    if (!class_addMethod(meta, select, (IMP)observe_coordinator, method_getTypeEncoding(factory)))
        method_setImplementation(factory, (IMP)observe_coordinator);
    receipt("PONG APPS: native coordinator observer active thread_persona=%d\n", threadPersonaFallback);
}
