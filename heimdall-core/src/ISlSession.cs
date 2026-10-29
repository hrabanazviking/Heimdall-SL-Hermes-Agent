using System.Text.Json.Nodes;

namespace Heimdall.Core;

/// <summary>
/// The body behind the seam. Two implementations exist:
/// <see cref="SlSession"/> (the real LibreMetaverse client) and
/// <see cref="SimulatedSession"/> (an explicitly opted-in offline
/// fixture whose every event is stamped <c>simulated: true</c>).
/// Events are §7.3 wire objects: a JSON object whose <c>event</c> key
/// carries the Core event name (ChatReceived, IMReceived, …); the
/// Python Bifröst side normalises them onto the canonical seam names.
/// </summary>
public interface ISlSession : IDisposable
{
    /// <summary>Raised for every outbound §7.3 event.</summary>
    event Action<JsonObject>? EventEmitted;

    bool LoggedIn { get; }
    bool Simulated { get; }
    string? AgentName { get; }
    string? AgentUuid { get; }
    string? RegionName { get; }

    /// <summary>Attempt login with the process credentials.</summary>
    /// <returns>Null on success; otherwise the seam error string.</returns>
    string? Login();

    void Logout();
    string? SendChat(string message, int channel, string chatType);
    string? SendIM(string toUuid, string message);
    string? SendGroupIM(string groupUuid, string message);
    string? Teleport(string region, double x, double y, double z);
    string? Sit(string targetUuid);
    string? Stand();
    JsonObject Status();
}
