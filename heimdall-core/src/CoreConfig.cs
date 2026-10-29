namespace Heimdall.Core;

/// <summary>
/// Process configuration, from the environment only.
///
/// Credentials rule (build law): the Second Life password reaches this
/// process ONLY through environment variables provisioned out-of-band
/// by the operator (the Secure Vault flow) — never through a config
/// file, never through an IPC command, never through chat. If the
/// variables are absent the Core still runs: its IPC contract is live
/// and the login command fails closed with <c>credentials_unavailable</c>.
/// </summary>
public sealed record CoreConfig(
    string CommandSocket,
    string EventSocket,
    string LoginUri,
    string? FirstName,
    string? LastName,
    string? Password,
    string StartLocation,
    bool Simulated)
{
    public bool HasCredentials =>
        !string.IsNullOrEmpty(FirstName) && !string.IsNullOrEmpty(LastName) && !string.IsNullOrEmpty(Password);

    public static CoreConfig FromEnvironment(bool simulatedFlag)
    {
        string? Env(string name) =>
            Environment.GetEnvironmentVariable(name) is { Length: > 0 } value ? value : null;

        var commandSocket = Env("HEIMDALL_CMD_SOCK") ?? Env("HEIMDALL_CMD_SOCKET") ?? "/tmp/heimdall.sock";
        return new CoreConfig(
            CommandSocket: commandSocket,
            EventSocket: Env("HEIMDALL_EVENT_SOCK") ?? Env("HEIMDALL_EVT_SOCKET") ?? commandSocket + ".events",
            LoginUri: Env("HEIMDALL_SL_LOGIN_URI") ?? "https://login.agni.lindenlab.com/cgi-bin/login.cgi",
            FirstName: Env("HEIMDALL_SL_FIRST_NAME"),
            LastName: Env("HEIMDALL_SL_LAST_NAME"),
            Password: Env("HEIMDALL_SL_PASSWORD"),
            StartLocation: Env("HEIMDALL_SL_START") ?? "last",
            Simulated: simulatedFlag || Env("HEIMDALL_SIMULATED") == "1");
    }
}
