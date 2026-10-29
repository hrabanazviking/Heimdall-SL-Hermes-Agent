namespace Heimdall.Core;

/// <summary>
/// Hosts the body behind the seam: builds the session (the real
/// LibreMetaverse-backed <see cref="SlSession"/> wrapper, or the
/// explicitly opted-in <see cref="SimulatedSession"/> fixture),
/// wires its §7.3 events onto the IPC event socket, and serves
/// commands. The seam comes up first and stays up even with no
/// credentials, so the Python Bifröst side can always reach status
/// and hear the honest answer.
/// </summary>
public sealed class CoreHost : IDisposable
{
    private readonly ISlSession _session;
    private readonly IpcServer _ipc;

    public CoreHost(CoreConfig config)
    {
        Config = config;
        _session = config.Simulated
            ? new SimulatedSession()
            : new SlSession(config);
        _ipc = new IpcServer(config.CommandSocket, config.EventSocket,
            new CommandProcessor(_session));
        _session.EventEmitted += _ipc.Broadcast;
    }

    public CoreConfig Config { get; }
    public ISlSession Session => _session;

    public void Start() => _ipc.Start();

    /// <summary>
    /// Startup login, attempted ONLY when the operator provisioned
    /// credentials through the process environment. With no env
    /// credentials this is a no-op: no network login is attempted.
    /// Returns the seam error string on failure, else null.
    /// </summary>
    public string? TryStartupLogin()
    {
        if (Config.Simulated || !Config.HasCredentials) return null;
        return _session.Login();
    }

    public void Dispose()
    {
        _session.EventEmitted -= _ipc.Broadcast;
        _ipc.Dispose();
        _session.Dispose();
    }
}
