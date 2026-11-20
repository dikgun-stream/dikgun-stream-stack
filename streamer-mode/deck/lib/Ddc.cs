using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

// Minimal DDC/CI access for physical monitors.
//
// The OS exposes monitors as HMONITOR; DDC/CI needs an HPHYSICALMONITOR per
// display. Getting from one to the other is what MonitorFromPoint +
// GetPhysicalMonitorsFromHMONITOR is for. Everything here is read-only apart
// from SetVcp, which is only called when explicitly asked for.
public static class Ddc
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int left, top, right, bottom; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct PHYSICAL_MONITOR
    {
        public IntPtr hPhysicalMonitor;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string szPhysicalMonitorDescription;
    }

    [DllImport("user32.dll")]
    static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorEnumProc proc, IntPtr data);
    delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, ref RECT rect, IntPtr data);

    [DllImport("dxva2.dll", SetLastError = true)]
    static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr hMonitor, out uint count);

    [DllImport("dxva2.dll", SetLastError = true)]
    static extern bool GetPhysicalMonitorsFromHMONITOR(
        IntPtr hMonitor, uint count,
        [Out, MarshalAs(UnmanagedType.LPArray, ArraySubType = UnmanagedType.Struct)] PHYSICAL_MONITOR[] monitors);

    [DllImport("dxva2.dll", SetLastError = true)]
    static extern bool DestroyPhysicalMonitor(IntPtr hMonitor);

    [DllImport("dxva2.dll", SetLastError = true)]
    static extern bool GetVCPFeatureAndVCPFeatureReply(
        IntPtr hMonitor, byte code, out uint type, out uint current, out uint maximum);

    [DllImport("dxva2.dll", SetLastError = true)]
    static extern bool SetVCPFeature(IntPtr hMonitor, byte code, uint value);

    [DllImport("dxva2.dll", SetLastError = true)]
    static extern bool GetCapabilitiesStringLength(IntPtr hMonitor, out uint length);

    [DllImport("dxva2.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern bool CapabilitiesRequestAndCapabilitiesReply(
        IntPtr hMonitor, IntPtr buffer, uint length);

    public class Monitor
    {
        public IntPtr Handle;
        public string Description;
        public RECT Bounds;
    }

    public static List<Monitor> Enumerate()
    {
        var found = new List<Monitor>();
        EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (IntPtr hMon, IntPtr hdc, ref RECT r, IntPtr d) =>
        {
            uint count;
            if (GetNumberOfPhysicalMonitorsFromHMONITOR(hMon, out count) && count > 0)
            {
                var arr = new PHYSICAL_MONITOR[count];
                if (GetPhysicalMonitorsFromHMONITOR(hMon, count, arr))
                {
                    foreach (var pm in arr)
                    {
                        found.Add(new Monitor
                        {
                            Handle = pm.hPhysicalMonitor,
                            Description = pm.szPhysicalMonitorDescription,
                            Bounds = r
                        });
                    }
                }
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static string Capabilities(IntPtr hMonitor)
    {
        uint len;
        if (!GetCapabilitiesStringLength(hMonitor, out len) || len == 0) return null;
        IntPtr buf = Marshal.AllocHGlobal((int)len);
        try
        {
            if (!CapabilitiesRequestAndCapabilitiesReply(hMonitor, buf, len)) return null;
            return Marshal.PtrToStringAnsi(buf);
        }
        finally { Marshal.FreeHGlobal(buf); }
    }

    public static bool TryGetVcp(IntPtr hMonitor, byte code, out uint current, out uint maximum)
    {
        current = 0; maximum = 0;
        uint type;
        return GetVCPFeatureAndVCPFeatureReply(hMonitor, code, out type, out current, out maximum);
    }

    public static bool SetVcp(IntPtr hMonitor, byte code, uint value)
    {
        return SetVCPFeature(hMonitor, code, value);
    }

    public static void Release(IntPtr hMonitor)
    {
        DestroyPhysicalMonitor(hMonitor);
    }
}
