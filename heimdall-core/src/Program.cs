using Heimdall.Core;

// Heimdall.Core — the Second Life body (Phase 2, slice 2).
//
// Posture at startup: the IPC seam comes up first and stays up even
// with no credentials, so the Python Bifröst side can always reach
// status and hear the honest answer. Login happens only when the
// operator provisioned credentials through the process environment
// (Secure Vault flow) — automatically at startup, or later through
// the login command. Nothing here ever asks for a password, reads one
// from a file, or accepts one over the socket. Phase 1's grid
// login/stand/hear/send exit test stays BLOCKED until credentials
// arrive through that flow; this binary never attempts a login
// without them.

var simulated = args.Contains("--simulated");
var config = CoreConfig.FromEnvironment(simulated);

using var host = new CoreHost(config);
host.Start();
var session = host.Session;

Console.WriteLine(session.Simulated
    ? "heimdall-core: simulated session (offline fixture; events stamped simulated:true)"
    : "heimdall-core: real session armed");
Console.WriteLine($"heimdall-core: commands on {config.CommandSocket}, events on {config.EventSocket}");

var startupFailure = host.TryStartupLogin();
if (!config.Simulated && config.HasCredentials)
{
    Console.WriteLine(startupFailure == null
        ? $"heimdall-core: logged in as {session.AgentName}"
        : $"heimdall-core: startup login failed: {startupFailure}");
}
else if (!config.Simulated)
{
    Console.WriteLine("heimdall-core: no credentials in environment — login will fail closed (credentials_unavailable)");
}

var done = new TaskCompletionSource();
Console.CancelKeyPress += (_, e) => { e.Cancel = true; done.TrySetResult(); };
using var sigterm = System.Runtime.InteropServices.PosixSignalRegistration.Create(
    System.Runtime.InteropServices.PosixSignal.SIGTERM, _ => done.TrySetResult());
await done.Task;

session.Logout();
Console.WriteLine("heimdall-core: shut down cleanly");
