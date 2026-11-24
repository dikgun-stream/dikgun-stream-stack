// DeckLauncher: one tiny exe, copied under each action's name.
//
// The Stream Dock "open" action resolves .lnk files to their target and drops
// the arguments (observed: bare interactive pwsh opened on STREAM MODE). It
// treats .bat as an untrusted script (trust prompt, silently swallowed).
// A local .exe gets neither treatment.
//
// Each copy of this exe is named after an action (go-live.exe, stream-mode.exe,
// ...). On start it maps its own filename to the PowerShell command line and
// launches pwsh hidden. Unknown names write a marker file instead of doing
// nothing, so a renamed key fails loudly.
//
// Build:  powershell -File build-launchers.ps1   (compiles + copies per action)

using System;
using System.Diagnostics;
using System.IO;

public class DeckLauncher
{
    static string Args(string name)
    {
        string deck = @"C:\Users\JR\Documents\deepseek-harness\default-workspace\streamer-mode\deck";
        string act = deck + @"\Deck-Action.ps1";
        string mode = @"C:\Users\JR\Documents\deepseek-harness\default-workspace\streamer-mode\Streamer-Mode.ps1";

        // Deck-Action.ps1 actions
        switch (name)
        {
            case "assist":        return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action assist";
            case "chat":          return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action chat";
            case "damage":        return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action damage";
            case "dashboard":     return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action dashboard";
            case "go-live":       return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action golive";
            case "hint":          return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action hint";
            case "kill":          return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action kill";
            case "mic-toggle":    return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action mic";
            case "panic":         return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action panic";
            case "prep":          return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action prep";
            case "stop-live":     return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action stop";
            case "stream-all":    return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action streamall";
            case "tiktok":        return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action tiktok";
            case "tiktok-go":     return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action tiktokgo";
            case "tiktok-state":  return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action tiktokstate";
            case "tiktok-stop":   return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + act + "\" -Action tiktokstop";

            // Streamer-Mode.ps1
            case "stream-mode":   return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + mode + "\" -Mode stream";
            case "dev-mode":      return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + mode + "\" -Mode dev";
            case "restore":       return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + mode + "\" -Restore";

            // plain launchers
            case "launch-deadlock": return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + deck + @"\Launch-Deadlock.ps1" + "\"";
            case "launch-eft":      return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + deck + @"\Launch-EFT.ps1" + "\"";
            case "yt-live":         return "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"" + deck + @"\yt-live.ps1" + "\"";
        }
        return null;
    }

    public static void Main()
    {
        string name = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);
        string args = Args(name);
        string temp = Path.GetTempPath();
        if (args == null)
        {
            File.WriteAllText(temp + "deck-launcher-unknown.txt", name + " - no such action");
            return;
        }
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = @"C:\Program Files\PowerShell\7\pwsh.exe";
            psi.Arguments = args;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            File.WriteAllText(temp + "deck-launcher-" + name + ".log", ex.ToString());
        }
    }
}
