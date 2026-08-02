using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

class Launcher
{
    // --- Job Object: kill child processes when parent exits ---
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string name);

    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll")]
    static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr hObject);

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public int LimitFlags;
        public IntPtr MinimumWorkingSetSize;
        public IntPtr MaximumWorkingSetSize;
        public int ActiveProcessLimit;
        public long Affinity;
        public int PriorityClass;
        public int SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public IntPtr ProcessMemoryLimit;
        public IntPtr JobMemoryLimit;
        public IntPtr PeakProcessMemoryUsed;
        public IntPtr PeakJobMemoryUsed;
    }

    const int JobObjectExtendedLimitInformation = 9;
    const int JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    static IntPtr jobHandle;

    static void AssignToJob(IntPtr processHandle)
    {
        if (jobHandle != IntPtr.Zero)
            AssignProcessToJobObject(jobHandle, processHandle);
    }

    // --- Performance: priority + CPU affinity ---
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetLogicalProcessorInformationEx(int RelationshipType, IntPtr Buffer, ref int ReturnedLength);

    [StructLayout(LayoutKind.Sequential)]
    struct PROCESSOR_RELATIONSHIP
    {
        public byte Flags;
        public byte EfficiencyClass;
        public byte Reserved0;
        public byte Reserved1;
        public int GroupCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX
    {
        public int Relationship;
        public int Size;
        public PROCESSOR_RELATIONSHIP Processor;
    }

    const int RelationProcessorCore = 0x0;
    const int RelationNumaNode = 0x1;
    const int JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x20;
    const int JOB_OBJECT_LIMIT_AFFINITY = 0x10;
    const int ABOVE_NORMAL_PRIORITY_CLASS = 0x8000;
    const int HIGH_PRIORITY_CLASS = 0x80;
    const int REALTIME_PRIORITY_CLASS = 0x100;
    const int BELOW_NORMAL_PRIORITY_CLASS = 0x4000;
    const int IDLE_PRIORITY_CLASS = 0x40;
    const int NORMAL_PRIORITY_CLASS = 0x20;

    struct PerformanceSettings
    {
        public int Priority; // 0=Idle, 1=BelowNormal, 2=Normal, 3=AboveNormal, 4=High, 5=RealTime
        public bool PCoresOnly;
        public int GpuDevice; // 0=default, 1,2,...=specific GPU (DirectML) — legacy fallback
        public int VulkanDevice; // 0=default, 1,2,...=specific Vulkan GPU — legacy fallback
        public string GpuLuid; // "Low:High" hex — primary GPU identifier, resolves to both DXGI and Vulkan
        public int NumaMode; // 0=none, 1=single node, 2=first N nodes
        public int NumaNode; // node index (mode 1) or count (mode 2)

        public static PerformanceSettings Default { get { return new PerformanceSettings { Priority = 2, PCoresOnly = false, GpuDevice = 0, VulkanDevice = 0, GpuLuid = null, NumaMode = 0, NumaNode = 0 }; } }
    }

    struct GpuInfo
    {
        public string Name;
        public long AdapterLuid;
        public int DxgiIndex; // Original DXGI adapter index for DirectML
        public uint VendorId;
        public uint DeviceId;
        public uint SubSysId;
    }

    static string LuidToString(long luid)
    {
        uint low = (uint)(luid & 0xFFFFFFFF);
        uint high = (uint)((luid >> 32) & 0xFFFFFFFF);
        return string.Format("{0:X8}:{1:X8}", low, high);
    }

    static int FindGpuIndexByLuid(List<GpuInfo> gpus, string luid)
    {
        if (string.IsNullOrEmpty(luid)) return -1;
        for (int i = 0; i < gpus.Count; i++)
            if (LuidToString(gpus[i].AdapterLuid) == luid) return i;
        return -1;
    }

    static int FindVulkanIndexByLuid(List<VulkanDeviceInfo> devices, string luid)
    {
        if (string.IsNullOrEmpty(luid)) return -1;
        foreach (var d in devices)
            if (d.Kind == "gpu" && d.Luid == luid) return d.Index;
        return -1;
    }

    delegate int DxgiEnumAdapters(IntPtr self, uint index, out IntPtr adapter);
    delegate int DxgiGetDesc(IntPtr self, out DXGI_ADAPTER_DESC desc);

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential, CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    struct DXGI_ADAPTER_DESC
    {
        [System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.ByValTStr, SizeConst = 128)]
        public string Description;
        public uint VendorId;
        public uint DeviceId;
        public uint SubSysId;
        public uint Revision;
        public UIntPtr DedicatedVideoMemory;
        public UIntPtr DedicatedSystemMemory;
        public UIntPtr SharedSystemMemory;
        public long AdapterLuid;
        public uint Flags; // DXGI_ADAPTER_FLAG_SOFTWARE = 0x2
    }

    [System.Runtime.InteropServices.DllImport("dxgi.dll", ExactSpelling = true)]
    static extern int CreateDXGIFactory1(ref Guid riid, out IntPtr ppFactory);

    static IntPtr DxgiVtableMethod(IntPtr obj, int index)
    {
        IntPtr vtable = System.Runtime.InteropServices.Marshal.ReadIntPtr(obj);
        return System.Runtime.InteropServices.Marshal.ReadIntPtr(vtable, index * IntPtr.Size);
    }

    static List<GpuInfo> DetectGpus()
    {
        var gpus = new List<GpuInfo>();
        try
        {
            Guid guid = new Guid("770aae78-f26f-4dba-a829-253c83d1b387");
            IntPtr factory;
            if (CreateDXGIFactory1(ref guid, out factory) != 0 || factory == IntPtr.Zero) return gpus;
            try
            {
                var enumAdapters = (DxgiEnumAdapters)System.Runtime.InteropServices.Marshal.GetDelegateForFunctionPointer(DxgiVtableMethod(factory, 7), typeof(DxgiEnumAdapters));
                uint i = 0;
                IntPtr adapter;
                while (enumAdapters(factory, i, out adapter) == 0)
                {
                    try
                    {
                        var getDesc = (DxgiGetDesc)System.Runtime.InteropServices.Marshal.GetDelegateForFunctionPointer(DxgiVtableMethod(adapter, 8), typeof(DxgiGetDesc));
                        DXGI_ADAPTER_DESC desc;
                        if (getDesc(adapter, out desc) == 0)
                            gpus.Add(new GpuInfo { Name = desc.Description ?? ("GPU " + i), AdapterLuid = desc.AdapterLuid, DxgiIndex = (int)i, VendorId = desc.VendorId, DeviceId = desc.DeviceId, SubSysId = desc.SubSysId });
                    }
                    finally { System.Runtime.InteropServices.Marshal.Release(adapter); }
                    i++;
                }
            }
            finally { System.Runtime.InteropServices.Marshal.Release(factory); }
        }
        catch { }
        return gpus;
    }

    struct VulkanDeviceInfo
    {
        public int Index;
        public string Name;
        public string Kind; // "vulkan" or "cpu"
        public string Luid; // "Low:High" hex or null
    }

    static List<VulkanDeviceInfo> DetectVulkanDevices()
    {
        var devices = new List<VulkanDeviceInfo>();
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string cliPath = Path.Combine(root, "bin", "transcribe-cli.exe");
            if (!File.Exists(cliPath)) return devices;

            var psi = new ProcessStartInfo
            {
                FileName = cliPath,
                Arguments = "--list-devices",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            };
            using (var proc = Process.Start(psi))
            {
                if (proc == null) return devices;
                string output = proc.StandardOutput.ReadToEnd();
                proc.WaitForExit(5000);

                string[] lines = output.Split('\n');
                for (int li = 0; li < lines.Length; li++)
                {
                    var trimmed = lines[li].Trim();
                    // Parse lines like: "  [0] AMD Radeon RX 9070 XT"
                    if (!trimmed.StartsWith("[")) continue;
                    var bracketEnd = trimmed.IndexOf(']');
                    if (bracketEnd < 0) continue;
                    int idx;
                    if (!int.TryParse(trimmed.Substring(1, bracketEnd - 1), out idx)) continue;
                    string name = trimmed.Substring(bracketEnd + 1).Trim();
                    string kind = "gpu";
                    if (name.Contains("CPU") || name.Contains("Processor"))
                        kind = "cpu";
                    // Parse LUID from next line: "name=Vulkan0  kind=vulkan  type=gpu  id=...  luid=XXXX:XXXX"
                    string luid = null;
                    if (li + 1 < lines.Length)
                    {
                        var detailLine = lines[li + 1].Trim();
                        var luidTag = "luid=";
                        var luidStart = detailLine.IndexOf(luidTag);
                        if (luidStart >= 0)
                        {
                            luidStart += luidTag.Length;
                            var luidEnd = detailLine.IndexOf(' ', luidStart);
                            luid = luidEnd > luidStart ? detailLine.Substring(luidStart, luidEnd - luidStart) : detailLine.Substring(luidStart);
                            if (luid == "(none)") luid = null;
                        }
                    }
                    devices.Add(new VulkanDeviceInfo { Index = idx, Name = name, Kind = kind, Luid = luid });
                }
            }
        }
        catch { }
        return devices;
    }

    [System.Runtime.InteropServices.DllImport("advapi32.dll")]
    static extern int RegFlushKey(IntPtr hKey);

    static void SetGpuPreferenceRegistry(int gpuIndex, List<GpuInfo> gpus, string dataDir)
    {
        try
        {
            string root = Path.GetDirectoryName(System.Diagnostics.Process.GetCurrentProcess().MainModule.FileName);
            string pythonExe = Path.Combine(root, "python", "python.exe");
            if (!File.Exists(pythonExe)) return;
            string regPath = @"SOFTWARE\Microsoft\DirectX\UserGpuPreferences";
            using (var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(regPath))
            {
                if (gpuIndex >= 0 && gpuIndex < gpus.Count)
                {
                    long luid = gpus[gpuIndex].AdapterLuid;
                    uint low = (uint)(luid & 0xFFFFFFFF);
                    int high = (int)(luid >> 32);
                    string val = string.Format("GpuPreference=LUID:0x{0:X8},0x{1:X8};", low, high);
                    key.SetValue(pythonExe, val, Microsoft.Win32.RegistryValueKind.String);
                }
                else
                {
                    key.DeleteValue(pythonExe, false);
                }
                RegFlushKey(key.Handle.DangerousGetHandle());
            }
        }
        catch { }
    }

    static int CountPCores()
    {
        return BuildPCoreAffinityMask().PCoreCount;
    }

    struct PCoreAffinityResult
    {
        public long Mask;
        public int PCoreCount;
        public int Group;
        public bool MultiGroup;
    }

    static PCoreAffinityResult BuildPCoreAffinityMask(PerformanceSettings? perf = null)
    {
        var result = new PCoreAffinityResult { Mask = 0, PCoreCount = 0, Group = 0, MultiGroup = false };
        var groupMasks = new System.Collections.Generic.Dictionary<int, long>();
        var groupCounts = new System.Collections.Generic.Dictionary<int, int>();

        // Determine which NUMA nodes to include
        System.Collections.Generic.HashSet<int> allowedNodes = null;
        if (perf.HasValue && perf.Value.NumaMode > 0)
        {
            var numaNodes = DetectNumaNodes();
            allowedNodes = new System.Collections.Generic.HashSet<int>();
            if (perf.Value.NumaMode == 1 && perf.Value.NumaNode < numaNodes.Length)
            {
                // Single node
                allowedNodes.Add(numaNodes[perf.Value.NumaNode].NodeNumber);
            }
            else if (perf.Value.NumaMode == 2)
            {
                // First N nodes
                for (int i = 0; i < Math.Min(perf.Value.NumaNode, numaNodes.Length); i++)
                    allowedNodes.Add(numaNodes[i].NodeNumber);
            }
        }

        try
        {
            int len = 0;
            GetLogicalProcessorInformationEx(RelationProcessorCore, IntPtr.Zero, ref len);
            IntPtr buf = Marshal.AllocHGlobal(len);
            try
            {
                if (!GetLogicalProcessorInformationEx(RelationProcessorCore, buf, ref len))
                {
                    result.PCoreCount = Environment.ProcessorCount;
                    result.Mask = (1L << Environment.ProcessorCount) - 1;
                    return result;
                }

                // Build core->numa mapping if NUMA filtering is active
                System.Collections.Generic.Dictionary<long, int> coreNumaMap = null;
                if (allowedNodes != null)
                {
                    coreNumaMap = BuildCoreToNumaMap();
                }

                int offset = 0;
                int coreIndex = 0;
                while (offset < len)
                {
                    var info = (SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX)Marshal.PtrToStructure(
                        new IntPtr(buf.ToInt64() + offset), typeof(SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX));
                    bool isPCore = info.Processor.EfficiencyClass > 0;
                    bool wantPCoreOnly = perf.HasValue && perf.Value.PCoresOnly;
                    bool wantNuma = perf.HasValue && perf.Value.NumaMode > 0;
                    bool includeCore = isPCore || (!wantPCoreOnly && wantNuma);
                    if (info.Relationship == RelationProcessorCore && includeCore)
                    {
                        // Check NUMA filter
                        if (allowedNodes != null && coreNumaMap != null)
                        {
                            int nodeNum;
                            if (coreNumaMap.TryGetValue(coreIndex, out nodeNum) && !allowedNodes.Contains(nodeNum))
                            {
                                coreIndex++;
                                offset += info.Size;
                                continue;
                            }
                        }

                        result.PCoreCount++;
                        int gaOffset = offset + 8;
                        int groupCount = info.Processor.GroupCount;
                        for (int g = 0; g < groupCount; g++)
                        {
                            long mask = IntPtr.Size == 8
                                ? Marshal.ReadInt64(new IntPtr(buf.ToInt64() + gaOffset))
                                : Marshal.ReadInt32(new IntPtr(buf.ToInt64() + gaOffset));
                            int group = Marshal.ReadInt16(new IntPtr(buf.ToInt64() + gaOffset + IntPtr.Size));
                            if (!groupMasks.ContainsKey(group)) { groupMasks[group] = 0; groupCounts[group] = 0; }
                            groupMasks[group] |= mask;
                            int bits = 0;
                            for (int b = 0; b < 64; b++) if ((mask & (1L << b)) != 0) bits++;
                            groupCounts[group] += bits;
                            gaOffset += IntPtr.Size + 8;
                        }
                    }
                    if (info.Relationship == RelationProcessorCore) coreIndex++;
                    offset += info.Size;
                }
            }
            finally { Marshal.FreeHGlobal(buf); }
        }
        catch
        {
            result.PCoreCount = Environment.ProcessorCount;
            result.Mask = (1L << Environment.ProcessorCount) - 1;
            return result;
        }
        if (result.PCoreCount == 0)
        {
            result.PCoreCount = Environment.ProcessorCount;
            result.Mask = (1L << Environment.ProcessorCount) - 1;
            return result;
        }
        if (groupMasks.Count <= 1)
        {
            foreach (var kv in groupMasks) { result.Mask = kv.Value; result.Group = kv.Key; }
        }
        else
        {
            result.MultiGroup = true;
            int bestGroup = 0; long bestMask = 0; int bestCount = 0;
            foreach (var kv in groupMasks)
            {
                int count = 0;
                for (int b = 0; b < 64; b++) if ((kv.Value & (1L << b)) != 0) count++;
                if (count > bestCount) { bestCount = count; bestMask = kv.Value; bestGroup = kv.Key; }
            }
            result.Mask = bestMask;
            result.Group = bestGroup;
        }
        return result;
    }

    static System.Collections.Generic.Dictionary<long, int> BuildCoreToNumaMap()
    {
        var map = new System.Collections.Generic.Dictionary<long, int>();
        try
        {
            int len = 0;
            GetLogicalProcessorInformationEx(RelationNumaNode, IntPtr.Zero, ref len);
            IntPtr buf = Marshal.AllocHGlobal(len);
            try
            {
                if (!GetLogicalProcessorInformationEx(RelationNumaNode, buf, ref len))
                    return map;
                int offset = 0;
                while (offset < len)
                {
                    int relType = Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset));
                    int entrySize = Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset + 4));
                    if (relType == RelationNumaNode)
                    {
                        int nodeNumber = Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset + 8));
                        long mask = IntPtr.Size == 8
                            ? Marshal.ReadInt64(new IntPtr(buf.ToInt64() + offset + 36))
                            : Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset + 36));
                        for (int b = 0; b < 64; b++)
                            if ((mask & (1L << b)) != 0)
                                map[b] = nodeNumber;
                    }
                    offset += entrySize > 0 ? entrySize : 64;
                }
            }
            finally { Marshal.FreeHGlobal(buf); }
        }
        catch { }
        return map;
    }

    struct NumaNodeInfo
    {
        public int NodeNumber;
        public int Group;
        public long Mask;
        public int CoreCount;
    }

    static NumaNodeInfo[] DetectNumaNodes()
    {
        var nodes = new System.Collections.Generic.List<NumaNodeInfo>();
        try
        {
            int len = 0;
            GetLogicalProcessorInformationEx(RelationNumaNode, IntPtr.Zero, ref len);
            IntPtr buf = Marshal.AllocHGlobal(len);
            try
            {
                if (!GetLogicalProcessorInformationEx(RelationNumaNode, buf, ref len))
                    return nodes.ToArray();
                int offset = 0;
                while (offset < len)
                {
                    // Read relationship type at current offset
                    int relType = Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset));
                    int entrySize = Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset + 4));
                    if (relType == RelationNumaNode)
                    {
                        // NUMA_NODE_RELATIONSHIP: NodeNumber(uint) + Reserved(28) + GROUP_AFFINITY
                        int nodeNumber = Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset + 8));
                        // GROUP_AFFINITY at offset+8+28 = offset+36
                        long mask = IntPtr.Size == 8
                            ? Marshal.ReadInt64(new IntPtr(buf.ToInt64() + offset + 36))
                            : Marshal.ReadInt32(new IntPtr(buf.ToInt64() + offset + 36));
                        int group = Marshal.ReadInt16(new IntPtr(buf.ToInt64() + offset + 36 + IntPtr.Size));
                        int bits = 0;
                        for (int b = 0; b < 64; b++) if ((mask & (1L << b)) != 0) bits++;
                        nodes.Add(new NumaNodeInfo { NodeNumber = nodeNumber, Group = group, Mask = mask, CoreCount = bits });
                    }
                    offset += entrySize > 0 ? entrySize : 64;
                }
            }
            finally { Marshal.FreeHGlobal(buf); }
        }
        catch { }
        return nodes.ToArray();
    }

    static int PriorityToClass(int priority)
    {
        switch (priority)
        {
            case 0: return IDLE_PRIORITY_CLASS;
            case 1: return BELOW_NORMAL_PRIORITY_CLASS;
            case 2: return NORMAL_PRIORITY_CLASS;
            case 3: return ABOVE_NORMAL_PRIORITY_CLASS;
            case 4: return HIGH_PRIORITY_CLASS;
            case 5: return REALTIME_PRIORITY_CLASS;
            default: return NORMAL_PRIORITY_CLASS;
        }
    }

    static PerformanceSettings LoadPerformanceSettings(string dataDir)
    {
        var s = PerformanceSettings.Default;
        string path = Path.Combine(dataDir, "performance.dat");
        if (!File.Exists(path)) return s;
        try
        {
            foreach (string raw in File.ReadAllLines(path))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line[0] == '#') continue;
                int eq = line.IndexOf('=');
                if (eq < 0) continue;
                string key = line.Substring(0, eq).Trim().ToLowerInvariant();
                string val = line.Substring(eq + 1).Trim();
                if (key == "priority")
                {
                    int v;
                    if (int.TryParse(val, out v) && v >= 0 && v <= 5) s.Priority = v;
                }
                else if (key == "pcores_only")
                {
                    s.PCoresOnly = val == "1" || val.ToLowerInvariant() == "true";
                }
                else if (key == "gpu_device")
                {
                    int v;
                    if (int.TryParse(val, out v) && v >= 0) s.GpuDevice = v;
                }
                else if (key == "vulkan_device")
                {
                    int v;
                    if (int.TryParse(val, out v) && v >= 0) s.VulkanDevice = v;
                }
                else if (key == "gpu_luid")
                {
                    if (!string.IsNullOrEmpty(val)) s.GpuLuid = val;
                }
                else if (key == "numa_mode")
                {
                    int v;
                    if (int.TryParse(val, out v) && v >= 0 && v <= 2) s.NumaMode = v;
                }
                else if (key == "numa_node")
                {
                    int v;
                    if (int.TryParse(val, out v) && v >= 0) s.NumaNode = v;
                }
            }
        }
        catch { }
        return s;
    }

    static void SavePerformanceSettings(string dataDir, PerformanceSettings s)
    {
        try
        {
            Directory.CreateDirectory(dataDir);
            File.WriteAllText(Path.Combine(dataDir, "performance.dat"),
                "# PuriPuly Heart performance settings\n" +
                "priority=" + s.Priority + "\n" +
                "pcores_only=" + (s.PCoresOnly ? "1" : "0") + "\n" +
                "gpu_luid=" + (s.GpuLuid ?? "") + "\n" +
                "numa_mode=" + s.NumaMode + "\n" +
                "numa_node=" + s.NumaNode + "\n");
        }
        catch { }
    }

    static void SetupKillOnClose()
    {
        SetupKillOnCloseWithPerf(PerformanceSettings.Default);
    }

    static void SetupKillOnCloseWithPerf(PerformanceSettings perf)
    {
        jobHandle = CreateJobObject(IntPtr.Zero, null);
        if (jobHandle == IntPtr.Zero) return;

        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PRIORITY_CLASS;
        info.BasicLimitInformation.PriorityClass = PriorityToClass(perf.Priority);

        if (perf.PCoresOnly || perf.NumaMode > 0)
        {
            var pcoreInfo = BuildPCoreAffinityMask(perf);
            if (pcoreInfo.Mask != 0)
            {
                info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_AFFINITY;
                info.BasicLimitInformation.Affinity = pcoreInfo.Mask;
            }
        }

        int size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr ptr = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(info, ptr, false);
            SetInformationJobObject(jobHandle, JobObjectExtendedLimitInformation, ptr, (uint)size);
        }
        finally { Marshal.FreeHGlobal(ptr); }
    }
    // --- Theme detection ---
    static bool IsDarkTheme()
    {
        try
        {
            using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
            {
                if (key != null)
                {
                    object val = key.GetValue("AppsUseLightTheme");
                    if (val is int) return (int)val == 0;
                }
            }
        }
        catch { }
        return true; // default dark
    }

    // Color schemes
    static Color BG, BG2, BG3, FG, FG2, FG3, ACCENT_PINK, ACCENT_GREEN, ACCENT_BLUE, ACCENT_YELLOW, ACCENT_RED;

    static void InitColors(bool dark)
    {
        if (dark)
        {
            BG = Color.FromArgb(28, 28, 32);
            BG2 = Color.FromArgb(36, 36, 40);
            BG3 = Color.FromArgb(44, 44, 48);
            FG = Color.FromArgb(230, 230, 230);
            FG2 = Color.FromArgb(170, 170, 178);
            FG3 = Color.FromArgb(160, 160, 170);
            ACCENT_PINK = Color.FromArgb(240, 128, 180);
            ACCENT_GREEN = Color.FromArgb(100, 230, 140);
            ACCENT_BLUE = Color.FromArgb(130, 180, 255);
            ACCENT_YELLOW = Color.FromArgb(255, 200, 80);
            ACCENT_RED = Color.FromArgb(255, 120, 120);
        }
        else
        {
            BG = Color.FromArgb(242, 242, 246);
            BG2 = Color.FromArgb(230, 230, 236);
            BG3 = Color.FromArgb(218, 218, 226);
            FG = Color.FromArgb(30, 30, 30);
            FG2 = Color.FromArgb(70, 70, 78);
            FG3 = Color.FromArgb(100, 100, 110);
            ACCENT_PINK = Color.FromArgb(200, 60, 120);
            ACCENT_GREEN = Color.FromArgb(40, 160, 80);
            ACCENT_BLUE = Color.FromArgb(50, 100, 200);
            ACCENT_YELLOW = Color.FromArgb(180, 130, 20);
            ACCENT_RED = Color.FromArgb(200, 50, 50);
        }
    }

    static readonly string[][] MODELS = {
        new[] { "qwen3-asr-0.6b-int8-sherpa", "Qwen3 ASR 0.6B INT8", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-0.6b/onnx/int8/", "onnx" },
        new[] { "qwen3-asr-1.7b-int8-sherpa", "Qwen3 ASR 1.7B INT8", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-1.7b/onnx/int8/", "onnx" },
        new[] { "gigaam-v3-e2e-rnnt", "GigaAM v3 RNNT", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/gigaam-v3/onnx/int8/", "onnx" },
        new[] { "parakeet-tdt-0.6b-v3-int8", "Parakeet v3 Multilingual INT8", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/parakeet-tdt-0.6b-v3/onnx/int8/", "onnx" },
        new[] { "gigaam-v3-e2e-rnnt-gguf", "GigaAM v3 GGUF", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/gigaam-v3/gguf/q8_0/", "gguf" },
        new[] { "parakeet-tdt-0.6b-v3-gguf", "Parakeet Multilingual GGUF", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/parakeet-tdt-0.6b-v3/gguf/q8_0/", "gguf" },
        new[] { "Qwen3-ASR-0.6B-gguf", "Qwen3 ASR 0.6B GGUF", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-0.6b/gguf/q8_0/", "gguf" },
        new[] { "qwen3-asr-1.7b-gguf", "Qwen3 ASR 1.7B GGUF", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-1.7b/gguf/q8_0/", "gguf" },
        new[] { "gigaam-v3-e2e-rnnt-gguf-q6k", "GigaAM v3 GGUF Q6_K", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/gigaam-v3/gguf/q6_k/", "gguf" },
        new[] { "gigaam-v3-e2e-rnnt-gguf-f16", "GigaAM v3 GGUF F16", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/gigaam-v3/gguf/f16/", "gguf" },
        new[] { "parakeet-tdt-0.6b-v3-gguf-q6k", "Parakeet Multilingual GGUF Q6_K", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/parakeet-tdt-0.6b-v3/gguf/q6_k/", "gguf" },
        new[] { "parakeet-tdt-0.6b-v3-gguf-f16", "Parakeet Multilingual GGUF F16", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/parakeet-tdt-0.6b-v3/gguf/f16/", "gguf" },
        new[] { "Qwen3-ASR-0.6B-gguf-q6k", "Qwen3 ASR 0.6B GGUF Q6_K", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-0.6b/gguf/q6_k/", "gguf" },
        new[] { "Qwen3-ASR-0.6B-gguf-f16", "Qwen3 ASR 0.6B GGUF F16", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-0.6b/gguf/f16/", "gguf" },
        new[] { "qwen3-asr-1.7b-gguf-q6k", "Qwen3 ASR 1.7B GGUF Q6_K", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-1.7b/gguf/q6_k/", "gguf" },
        new[] { "qwen3-asr-1.7b-gguf-f16", "Qwen3 ASR 1.7B GGUF F16", "https://huggingface.co/fzcfweasdferttgg/asr-gguf-onnx/resolve/main/qwen3-asr-1.7b/gguf/f16/", "gguf" },
    };
    static readonly string[][] MODEL_CATEGORIES = {
        new[] { "onnx", "ONNX" },
        new[] { "gguf", "GGUF" },
    };
    static readonly Dictionary<string, string> INSTALL_DIR_MAP = new Dictionary<string, string>() {
        { "qwen3-asr-0.6b-int8-sherpa", "qwen3-asr-0.6b" + Path.DirectorySeparatorChar + "onnx" + Path.DirectorySeparatorChar + "int8" },
        { "qwen3-asr-1.7b-int8-sherpa", "qwen3-asr-1.7b" + Path.DirectorySeparatorChar + "onnx" + Path.DirectorySeparatorChar + "int8" },
        { "gigaam-v3-e2e-rnnt",          "gigaam-v3" + Path.DirectorySeparatorChar + "onnx" + Path.DirectorySeparatorChar + "int8" },
        { "parakeet-tdt-0.6b-v3-int8",   "parakeet-tdt-0.6b-v3" + Path.DirectorySeparatorChar + "onnx" + Path.DirectorySeparatorChar + "int8" },
        { "gigaam-v3-e2e-rnnt-gguf",     "gigaam-v3" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q8_0" },
        { "parakeet-tdt-0.6b-v3-gguf",   "parakeet-tdt-0.6b-v3" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q8_0" },
        { "Qwen3-ASR-0.6B-gguf",         "qwen3-asr-0.6b" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q8_0" },
        { "qwen3-asr-1.7b-gguf",         "qwen3-asr-1.7b" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q8_0" },
        { "gigaam-v3-e2e-rnnt-gguf-q6k", "gigaam-v3" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q6_k" },
        { "gigaam-v3-e2e-rnnt-gguf-f16", "gigaam-v3" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "f16" },
        { "parakeet-tdt-0.6b-v3-gguf-q6k", "parakeet-tdt-0.6b-v3" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q6_k" },
        { "parakeet-tdt-0.6b-v3-gguf-f16", "parakeet-tdt-0.6b-v3" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "f16" },
        { "Qwen3-ASR-0.6B-gguf-q6k",    "qwen3-asr-0.6b" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q6_k" },
        { "Qwen3-ASR-0.6B-gguf-f16",    "qwen3-asr-0.6b" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "f16" },
        { "qwen3-asr-1.7b-gguf-q6k",    "qwen3-asr-1.7b" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "q6_k" },
        { "qwen3-asr-1.7b-gguf-f16",    "qwen3-asr-1.7b" + Path.DirectorySeparatorChar + "gguf" + Path.DirectorySeparatorChar + "f16" },
    };
    static string ModelInstallDir(int mi) {
        string id = MODELS[mi][0];
        string mapped;
        return INSTALL_DIR_MAP.TryGetValue(id, out mapped) ? mapped : id;
    }
    static readonly string[][] MODELS_LANGS = {
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "ru" },
        new[] { "en", "es", "fr", "de", "bg", "hr", "cs", "da", "nl", "et", "fi", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "sv", "ru", "uk" },
        new[] { "ru" },
        new[] { "en", "es", "fr", "de", "bg", "hr", "cs", "da", "nl", "et", "fi", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "sv", "ru", "uk" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "ru" },
        new[] { "ru" },
        new[] { "en", "es", "fr", "de", "bg", "hr", "cs", "da", "nl", "et", "fi", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "sv", "ru", "uk" },
        new[] { "en", "es", "fr", "de", "bg", "hr", "cs", "da", "nl", "et", "fi", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "sv", "ru", "uk" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
        new[] { "zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "vi", "th", "tr", "pl", "nl", "sv", "da", "fi", "nb", "cs", "sk", "hu", "ro", "bg", "hr", "sl", "el", "et", "lv", "lt", "uk", "ms", "id", "ca" },
    };
    static readonly string[][] LANG_NAMES = {
        new[] { "en", "English" }, new[] { "ru", "\u0420\u0443\u0441\u0441\u043A\u0438\u0439" }, new[] { "zh", "\u4E2D\u6587" }, new[] { "ja", "\u65E5\u672C\u8A9E" }, new[] { "ko", "\uD55C\uAD6D\uC5B4" },
        new[] { "fr", "Fran\u00E7ais" }, new[] { "de", "Deutsch" }, new[] { "es", "Espa\u00F1ol" }, new[] { "pt", "Portugu\u00EAs" }, new[] { "it", "Italiano" },
        new[] { "ar", "\u0627\u0644\u0639\u0631\u0628\u064A\u0629" }, new[] { "vi", "Ti\u1EBFng Vi\u1EC7t" }, new[] { "th", "\u0E44\u0E17\u0E22" }, new[] { "tr", "T\u00FCrk\u00E7e" },
        new[] { "pl", "Polski" }, new[] { "nl", "Nederlands" }, new[] { "sv", "Svenska" }, new[] { "da", "Dansk" }, new[] { "fi", "Suomi" }, new[] { "nb", "Norsk" },
        new[] { "cs", "\u010Ce\u0161tina" }, new[] { "sk", "Sloven\u010Dina" }, new[] { "hu", "Magyar" }, new[] { "ro", "Rom\u00E2n\u0103" }, new[] { "bg", "\u0411\u044A\u043B\u0433\u0430\u0440\u0441\u043A\u0438" },
        new[] { "hr", "Hrvatski" }, new[] { "sl", "Sloven\u0161\u010Dina" }, new[] { "el", "\u0395\u03BB\u03BB\u03B7\u03BD\u03B9\u03BA\u03AC" }, new[] { "et", "Eesti" }, new[] { "lv", "Latvie\u0161u" },
        new[] { "lt", "Lietuvi\u0173" }, new[] { "mt", "Malti" }, new[] { "uk", "\u0423\u043A\u0440\u0430\u0457\u043D\u0441\u044C\u043A\u0430" }, new[] { "ms", "Bahasa Melayu" }, new[] { "id", "Bahasa Indonesia" }, new[] { "ca", "Catal\u00E0" },
    };
    static readonly string[] MODELS_MODELSCOPE = {
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-0.6b/onnx/int8/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-1.7b/onnx/int8/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/gigaam-v3/onnx/int8/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/parakeet-tdt-0.6b-v3/onnx/int8/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/gigaam-v3/gguf/q8_0/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/parakeet-tdt-0.6b-v3/gguf/q8_0/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-0.6b/gguf/q8_0/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-1.7b/gguf/q8_0/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/gigaam-v3/gguf/q6_k/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/gigaam-v3/gguf/f16/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/parakeet-tdt-0.6b-v3/gguf/q6_k/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/parakeet-tdt-0.6b-v3/gguf/f16/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-0.6b/gguf/q6_k/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-0.6b/gguf/f16/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-1.7b/gguf/q6_k/",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx/resolve/master/qwen3-asr-1.7b/gguf/f16/",
    };
    static readonly string[] MODELS_MODELSCOPE_REPO = {
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
        "https://modelscope.cn/models/fzcfweasdferttggpng/asr-gguf-onnx",
    };
    static readonly string HF_MIRROR_BASE = "https://hf-mirror.com/";
    static readonly string[] REVISIONS = { "main", "main", "main", "main", "main", "main", "main", "main", "main", "main", "main", "main", "main", "main", "main", "main" };
    static readonly string[][] FILES = {
        new[] { "conv_frontend.onnx", "decoder.int8.onnx", "encoder.int8.onnx", "tokenizer/merges.txt", "tokenizer/tokenizer_config.json", "tokenizer/vocab.json" },
        new[] { "conv_frontend.onnx", "decoder.int8.onnx", "encoder.int8.onnx", "tokenizer/merges.txt", "tokenizer/tokenizer_config.json", "tokenizer/vocab.json" },
        new[] { "gigaam_v3_e2e_rnnt_encoder_int8.onnx", "gigaam_v3_e2e_rnnt_decoder.onnx", "gigaam_v3_e2e_rnnt_joint.onnx", "gigaam_v3_e2e_rnnt_tokens.txt" },
        new[] { "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt" },
        new[] { "gigaam-v3-e2e-rnnt-Q8_0.gguf" },
        new[] { "parakeet-tdt-0.6b-v3-Q8_0.gguf" },
        new[] { "Qwen3-ASR-0.6B-Q8_0.gguf" },
        new[] { "Qwen3-ASR-1.7B-Q8_0.gguf" },
        new[] { "gigaam-v3-e2e-rnnt-Q6_K.gguf" },
        new[] { "gigaam-v3-e2e-rnnt-F16.gguf" },
        new[] { "parakeet-tdt-0.6b-v3-Q6_K.gguf" },
        new[] { "parakeet-tdt-0.6b-v3-F16.gguf" },
        new[] { "Qwen3-ASR-0.6B-Q6_K.gguf" },
        new[] { "Qwen3-ASR-0.6B-F16.gguf" },
        new[] { "Qwen3-ASR-1.7B-Q6_K.gguf" },
        new[] { "Qwen3-ASR-1.7B-F16.gguf" },
    };
    static readonly string[][] LOCAL_FILES = {
        new[] { "conv_frontend.onnx", "decoder.int8.onnx", "encoder.int8.onnx", "tokenizer/merges.txt", "tokenizer/tokenizer_config.json", "tokenizer/vocab.json" },
        new[] { "conv_frontend.onnx", "decoder.int8.onnx", "encoder.int8.onnx", "tokenizer/merges.txt", "tokenizer/tokenizer_config.json", "tokenizer/vocab.json" },
        new[] { "encoder.onnx", "decoder.onnx", "joint.onnx", "tokens.txt" },
        new[] { "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt" },
        new[] { "gigaam-v3-e2e-rnnt-Q8_0.gguf" },
        new[] { "parakeet-tdt-0.6b-v3-Q8_0.gguf" },
        new[] { "Qwen3-ASR-0.6B-Q8_0.gguf" },
        new[] { "Qwen3-ASR-1.7B-Q8_0.gguf" },
        new[] { "gigaam-v3-e2e-rnnt-Q6_K.gguf" },
        new[] { "gigaam-v3-e2e-rnnt-F16.gguf" },
        new[] { "parakeet-tdt-0.6b-v3-Q6_K.gguf" },
        new[] { "parakeet-tdt-0.6b-v3-F16.gguf" },
        new[] { "Qwen3-ASR-0.6B-Q6_K.gguf" },
        new[] { "Qwen3-ASR-0.6B-F16.gguf" },
        new[] { "Qwen3-ASR-1.7B-Q6_K.gguf" },
        new[] { "Qwen3-ASR-1.7B-F16.gguf" },
    };
    static readonly long[][] SIZES = {
        new long[] { 44148281, 756563239, 182491662, 1671853, 12487, 2776833 },
        new long[] { 48080441, 2037458645, 314222162, 1671853, 12487, 2776833 },
        new long[] { 318995997, 4600058, 2712896, 13353 },
        new long[] { 652184281, 11845275, 6355277, 93939 },
        new long[] { 273724832 },
        new long[] { 739508576 },
        new long[] { 850423456 },
        new long[] { 2185030624 },
        new long[] { 227953952 },
        new long[] { 452381408 },
        new long[] { 610342240 },
        new long[] { 1255869856 },
        new long[] { 690417824 },
        new long[] { 1579793056 },
        new long[] { 1692554208 },
        new long[] { 4091390944 },
    };
    static readonly uint[][] CRC32S = {
        new uint[] { 0xCFA85A63, 0x0AD0DA54, 0xD7BCF214, 0x42998796, 0x1DCD864D, 0x8A9480F3 },
        new uint[] { 0x6AD3A6A9, 0xCE2DCC4E, 0xC40229D8, 0x42998796, 0x1DCD864D, 0x8A9480F3 },
        new uint[] { 0x4F92BA2D, 0xE4460B6B, 0x6BD70676, 0xD2A88681 },
        new uint[] { 0xBCE5FCAF, 0xE24198E2, 0xE1553E8A, 0xF72E20A6 },
        new uint[] { 0xC1218C26 },
        new uint[] { 0x667D99C1 },
        new uint[] { 0x4783845E },
        new uint[] { 0x924934A2 },
        new uint[] { 0xF99C4FD4 },
        new uint[] { 0xB0D124A8 },
        new uint[] { 0xA84E2513 },
        new uint[] { 0xCB7ADDA3 },
        new uint[] { 0xB36F0E75 },
        new uint[] { 0xAFC6FED2 },
        new uint[] { 0x29AAC2AC },
        new uint[] { 0x3817B077 },
    };

    static uint[] crcTable;
    static void InitCrcTable()
    {
        if (crcTable != null) return;
        crcTable = new uint[256];
        for (uint i = 0; i < 256; i++)
        {
            uint c = i;
            for (int j = 0; j < 8; j++)
                c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
            crcTable[i] = c;
        }
    }

    static uint ComputeCRC32(string filePath)
    {
        InitCrcTable();
        uint crc = 0xFFFFFFFF;
        using (var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            byte[] buf = new byte[65536];
            int bytesRead;
            while ((bytesRead = fs.Read(buf, 0, buf.Length)) > 0)
                for (int i = 0; i < bytesRead; i++)
                    crc = crcTable[(crc ^ buf[i]) & 0xFF] ^ (crc >> 8);
        }
        return crc ^ 0xFFFFFFFF;
    }

    static string Lang;
    static string ModelsDir;
    static Dictionary<string, Dictionary<string, string>> _translationsCache;

    static Dictionary<string, Dictionary<string, string>> BuildTranslations()
    {
        if (_translationsCache != null) return _translationsCache;
        var t = new Dictionary<string, Dictionary<string, string>>();

        t["ru"] = new Dictionary<string, string> { {"launch","Запустить"}, {"models","Модели"}, {"settings","Настройки"}, {"exit","Выход"}, {"download","Скачать"}, {"verify","Проверить"}, {"delete","Удалить"}, {"cancel","Отмена"}, {"src_hf","HuggingFace"}, {"src_mirror","Зеркало HF"}, {"src_ms","ModelScope"}, {"not_installed","Не установлена"}, {"installed","Установлена"}, {"incomplete","Неполная"}, {"preparing","Подготовка..."}, {"all_ok","Все файлы в порядке!"}, {"files_bad","файлов отсутствует или повреждены"}, {"confirm_delete","Удалить модель?"}, {"dl_failed","Ошибка загрузки:"}, {"downloading","Загрузка"}, {"size","Размер"}, {"on_disk","на диске"}, {"models_title","Управление моделями"}, {"model_status","Статус"}, {"file_damaged","файл повреждён"}, {"file_incomplete","файл не докачан"}, {"file_not_found","файл не найден"}, {"files_ok","файлов в порядке"}, {"files_with_problems","файлов с проблемами"}, {"redownload_question","Перекачать эти файлы?"}, {"file_0","Аудио-процессор"}, {"file_1","Декодер (основной, ~750 МБ)"}, {"file_2","Энкодер"}, {"file_3","Словарь слияний токенов"}, {"file_4","Настройки токенизатора"}, {"file_5","Словарь токенов"}, {"settings_title","Управление настройками"}, {"settings_backup","Сделать копию"}, {"settings_restore","Восстановить"}, {"settings_delete","Удалить настройки"}, {"settings_info","Файл настроек:"}, {"settings_no_file","Файл настроек не найден"}, {"settings_backup_ok","Копия сохранена"}, {"settings_restore_ok","Настройки восстановлены"}, {"settings_delete_confirm","Удалить все настройки программы?\nЭто действие нельзя отменить."}, {"settings_delete_ok","Настройки удалены"}, {"performance","Производительность"}, {"perf_title","Производительность"}, {"perf_tab_priority","Приоритет"}, {"perf_tab_cores","Ядра"}, {"perf_tab_gpu","GPU"}, {"perf_gpu_device","GPU для DirectML"}, {"perf_gpu_auto","Авто (GPU по умолчанию)"}, {"perf_gpu_hint","Выберите GPU для AI-инференса.\nТребуется перезапуск."}, {"perf_vulkan_device","Vulkan GPU"}, {"perf_vulkan_hint","Выберите Vulkan GPU для GGUF моделей (transcribe.cpp)."}, {"perf_gpu_restart_notice","Настройка GPU изменена. Лаунчер перезапустится для применения."}, {"perf_priority","Приоритет процесса"}, {"perf_priority_desc","Определяет приоритет всего дерева процессов.\nВыше нормального- для максимальной отзывчивости.\nНиже нормального- если приложение не должно мешать другим."}, {"perf_pcores","Использовать только P-ядра (производительные)"}, {"perf_pcores_hint","Автор рекомендует P-ядра для лучшей производительности.\nE-ядра могут вызывать задержки при обработке аудио."}, {"perf_save","Сохранить"}, {"perf_saved","Настройки производительности сохранены"}, {"priority_idle","Низкий"}, {"priority_below_normal","Ниже нормального"}, {"priority_normal","Нормальный"}, {"priority_above_normal","Выше нормального"}, {"priority_high","Высокий"}, {"priority_realtime","Реального времени"} };

        t["zh"] = new Dictionary<string, string> { {"launch","启动"}, {"models","模型"}, {"exit","退出"}, {"download","下载"}, {"verify","验证"}, {"delete","删除"}, {"cancel","取消"}, {"not_installed","未安装"}, {"installed","已安装"}, {"incomplete","不完整"}, {"preparing","准备中..."}, {"all_ok","所有文件验证通过！"}, {"files_bad","个文件缺失或损坏"}, {"confirm_delete","删除模型？"}, {"dl_failed","下载失败："}, {"downloading","下载中"}, {"size","大小"}, {"on_disk","已占用"}, {"models_title","模型管理"}, {"model_status","状态"}, {"file_damaged","文件损坏"}, {"file_incomplete","文件未下载完成"}, {"file_not_found","文件未找到"}, {"files_ok","个文件正常"}, {"files_with_problems","个文件有问题"}, {"redownload_question","重新下载这些文件？"}, {"priority_idle","低"}, {"priority_below_normal","低于正常"}, {"priority_normal","正常"}, {"priority_above_normal","高于正常"}, {"priority_high","高"}, {"priority_realtime","实时"} };

        t["de"] = new Dictionary<string, string> { {"launch","Starten"}, {"models","Modelle"}, {"exit","Beenden"}, {"download","Herunterladen"}, {"verify","Prüfen"}, {"delete","Löschen"}, {"cancel","Abbrechen"}, {"not_installed","Nicht installiert"}, {"installed","Installiert"}, {"incomplete","Unvollständig"}, {"preparing","Vorbereitung..."}, {"all_ok","Alle Dateien OK!"}, {"files_bad","Dateien fehlen oder sind beschädigt"}, {"confirm_delete","Modell löschen?"}, {"dl_failed","Download fehlgeschlagen:"}, {"downloading","Download"}, {"size","Größe"}, {"on_disk","auf Disk"}, {"models_title","Modellverwaltung"}, {"model_status","Status"}, {"file_damaged","Datei beschädigt"}, {"file_incomplete","Datei unvollständig"}, {"file_not_found","Datei nicht gefunden"}, {"files_ok","Dateien OK"}, {"files_with_problems","Dateien mit Problemen"}, {"redownload_question","Diese Dateien erneut herunterladen?"}, {"priority_idle","Niedrig"}, {"priority_below_normal","Unter Normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Über Normal"}, {"priority_high","Hoch"}, {"priority_realtime","Echtzeit"} };

        t["fr"] = new Dictionary<string, string> { {"launch","Lancer"}, {"models","Modèles"}, {"exit","Quitter"}, {"download","Télécharger"}, {"verify","Vérifier"}, {"delete","Supprimer"}, {"cancel","Annuler"}, {"not_installed","Non installé"}, {"installed","Installé"}, {"incomplete","Incomplet"}, {"preparing","Préparation..."}, {"all_ok","Tous les fichiers OK !"}, {"files_bad","fichiers manquants ou corrompus"}, {"confirm_delete","Supprimer le modèle ?"}, {"dl_failed","Échec du téléchargement :"}, {"downloading","Téléchargement"}, {"size","Taille"}, {"on_disk","sur le disque"}, {"models_title","Gestion des modèles"}, {"model_status","Statut"}, {"file_damaged","fichier endommagé"}, {"file_incomplete","fichier incomplet"}, {"file_not_found","fichier non trouvé"}, {"files_ok","fichiers OK"}, {"files_with_problems","fichiers avec problèmes"}, {"redownload_question","Re-télécharger ces fichiers ?"}, {"priority_idle","Bas"}, {"priority_below_normal","Inférieur à la normale"}, {"priority_normal","Normal"}, {"priority_above_normal","Supérieur à la normale"}, {"priority_high","Élevé"}, {"priority_realtime","Temps réel"} };

        t["es"] = new Dictionary<string, string> { {"launch","Iniciar"}, {"models","Modelos"}, {"exit","Salir"}, {"download","Descargar"}, {"verify","Verificar"}, {"delete","Eliminar"}, {"cancel","Cancelar"}, {"not_installed","No instalado"}, {"installed","Instalado"}, {"incomplete","Incompleto"}, {"preparing","Preparando..."}, {"all_ok","¡Todos los archivos OK!"}, {"files_bad","archivos faltantes o dañados"}, {"confirm_delete","¿Eliminar modelo?"}, {"dl_failed","Error de descarga:"}, {"downloading","Descargando"}, {"size","Tamaño"}, {"on_disk","en disco"}, {"models_title","Gestión de modelos"}, {"model_status","Estado"}, {"file_damaged","archivo dañado"}, {"file_incomplete","archivo incompleto"}, {"file_not_found","archivo no encontrado"}, {"files_ok","archivos OK"}, {"files_with_problems","archivos con problemas"}, {"redownload_question","¿Volver a descargar estos archivos?"}, {"priority_idle","Bajo"}, {"priority_below_normal","Por debajo de lo normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Por encima de lo normal"}, {"priority_high","Alto"}, {"priority_realtime","Tiempo real"} };

        t["pt"] = new Dictionary<string, string> { {"launch","Iniciar"}, {"models","Modelos"}, {"exit","Sair"}, {"download","Baixar"}, {"verify","Verificar"}, {"delete","Excluir"}, {"cancel","Cancelar"}, {"not_installed","Não instalado"}, {"installed","Instalado"}, {"incomplete","Incompleto"}, {"preparing","Preparando..."}, {"all_ok","Todos os arquivos OK!"}, {"files_bad","arquivos ausentes ou corrompidos"}, {"confirm_delete","Excluir modelo?"}, {"dl_failed","Falha no download:"}, {"downloading","Baixando"}, {"size","Tamanho"}, {"on_disk","no disco"}, {"models_title","Gerenciamento de modelos"}, {"model_status","Status"}, {"priority_idle","Baixo"}, {"priority_below_normal","Abaixo do normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Acima do normal"}, {"priority_high","Alto"}, {"priority_realtime","Tempo real"} };

        t["ja"] = new Dictionary<string, string> { {"launch","起動"}, {"models","モデル"}, {"exit","終了"}, {"download","ダウンロード"}, {"verify","検証"}, {"delete","削除"}, {"cancel","キャンセル"}, {"not_installed","未インストール"}, {"installed","インストール済み"}, {"incomplete","不完全"}, {"preparing","準備中..."}, {"all_ok","すべてのファイルが正常です！"}, {"files_bad","ファイルが見つからないか破損しています"}, {"confirm_delete","モデルを削除しますか？"}, {"dl_failed","ダウンロードエラー："}, {"downloading","ダウンロード中"}, {"size","サイズ"}, {"on_disk","使用中"}, {"models_title","モデル管理"}, {"model_status","ステータス"}, {"priority_idle","低"}, {"priority_below_normal","通常以下"}, {"priority_normal","通常"}, {"priority_above_normal","通常以上"}, {"priority_high","高"}, {"priority_realtime","リアルタイム"} };

        t["ko"] = new Dictionary<string, string> { {"launch","실행"}, {"models","모델"}, {"exit","종료"}, {"download","다운로드"}, {"verify","검증"}, {"delete","삭제"}, {"cancel","취소"}, {"not_installed","미설치"}, {"installed","설치됨"}, {"incomplete","불완전"}, {"preparing","준비 중..."}, {"all_ok","모든 파일이 정상입니다!"}, {"files_bad","파일이 없거나 손상됨"}, {"confirm_delete","모델을 삭제하시겠습니까?"}, {"dl_failed","다운로드 실패:"}, {"downloading","다운로드 중"}, {"size","크기"}, {"on_disk","디스크 사용"}, {"models_title","모델 관리"}, {"model_status","상태"}, {"priority_idle","낮음"}, {"priority_below_normal","보통 이하"}, {"priority_normal","보통"}, {"priority_above_normal","보통 이상"}, {"priority_high","높음"}, {"priority_realtime","실시간"} };

        t["ar"] = new Dictionary<string, string> { {"launch","تشغيل"}, {"models","نماذج"}, {"exit","خروج"}, {"download","تحميل"}, {"verify","تحقق"}, {"delete","حذف"}, {"cancel","إلغاء"}, {"not_installed","غير مثبت"}, {"installed","مثبت"}, {"incomplete","غير مكتمل"}, {"preparing","جاري التحضير..."}, {"all_ok","جميع الملفات سليمة!"}, {"files_bad","ملفات مفقودة أو تالفة"}, {"confirm_delete","حذف النموذج؟"}, {"dl_failed","فشل التحميل:"}, {"downloading","جاري التحميل"}, {"size","الحجم"}, {"on_disk","على القرص"}, {"models_title","إدارة النماذج"}, {"model_status","الحالة"}, {"priority_idle","منخفض"}, {"priority_below_normal","أقل من الطبيعي"}, {"priority_normal","طبيعي"}, {"priority_above_normal","أعلى من الطبيعي"}, {"priority_high","مرتفع"}, {"priority_realtime","الوقت الفعلي"} };

        t["tr"] = new Dictionary<string, string> { {"launch","Başlat"}, {"models","Modeller"}, {"exit","Çıkış"}, {"download","İndir"}, {"verify","Doğrula"}, {"delete","Sil"}, {"cancel","İptal"}, {"not_installed","Yüklü değil"}, {"installed","Yüklü"}, {"incomplete","Eksik"}, {"preparing","Hazırlanıyor..."}, {"all_ok","Tüm dosyalar tamam!"}, {"files_bad","dosya eksik veya bozuk"}, {"confirm_delete","Model silinsin mi?"}, {"dl_failed","İndirme hatası:"}, {"downloading","İndiriliyor"}, {"size","Boyut"}, {"on_disk","diskte"}, {"models_title","Model yönetimi"}, {"model_status","Durum"}, {"priority_idle","Düşük"}, {"priority_below_normal","Normal Altı"}, {"priority_normal","Normal"}, {"priority_above_normal","Normal Üstü"}, {"priority_high","Yüksek"}, {"priority_realtime","Gerçek Zamanlı"} };

        t["uk"] = new Dictionary<string, string> { {"launch","Запустити"}, {"models","Моделі"}, {"exit","Вихід"}, {"download","Завантажити"}, {"verify","Перевірити"}, {"delete","Видалити"}, {"cancel","Скасувати"}, {"not_installed","Не встановлено"}, {"installed","Встановлено"}, {"incomplete","Неповне"}, {"preparing","Підготовка..."}, {"all_ok","Усі файли в порядку!"}, {"files_bad","файлів відсутні або пошкоджені"}, {"confirm_delete","Видалити модель?"}, {"dl_failed","Помилка завантаження:"}, {"downloading","Завантаження"}, {"size","Розмір"}, {"on_disk","на диску"}, {"models_title","Управління моделями"}, {"model_status","Статус"}, {"priority_idle","Низький"}, {"priority_below_normal","Нижче нормального"}, {"priority_normal","Нормальний"}, {"priority_above_normal","Вище нормального"}, {"priority_high","Високий"}, {"priority_realtime","Реального часу"} };

        t["vi"] = new Dictionary<string, string> { {"launch","Khởi chạy"}, {"models","Mô hình"}, {"exit","Thoát"}, {"download","Tải xuống"}, {"verify","Kiểm tra"}, {"delete","Xóa"}, {"cancel","Hủy"}, {"not_installed","Chưa cài đặt"}, {"installed","Đã cài"}, {"incomplete","Chưa đầy đủ"}, {"preparing","Đang chuẩn bị..."}, {"all_ok","Tất cả tệp đều OK!"}, {"files_bad","tệp bị thiếu hoặc lỗi"}, {"confirm_delete","Xóa mô hình?"}, {"dl_failed","Lỗi tải:"}, {"downloading","Đang tải"}, {"size","Kích thước"}, {"on_disk","trên ổ đĩa"}, {"models_title","Quản lý mô hình"}, {"model_status","Trạng thái"}, {"priority_idle","Thấp"}, {"priority_below_normal","Dưới bình thường"}, {"priority_normal","Bình thường"}, {"priority_above_normal","Trên bình thường"}, {"priority_high","Cao"}, {"priority_realtime","Thời gian thực"} };

        t["hi"] = new Dictionary<string, string> { {"launch","शुरू करें"}, {"models","मॉडल"}, {"exit","बाहर"}, {"download","डाउनलोड"}, {"verify","सत्यापित करें"}, {"delete","हटाएं"}, {"cancel","रद्द करें"}, {"not_installed","स्थापित नहीं"}, {"installed","स्थापित"}, {"incomplete","अपूर्ण"}, {"preparing","तैयारी हो रही है..."}, {"all_ok","सभी फ़ाइलें ठीक हैं!"}, {"files_bad","फ़ाइलें गुम या दूषित"}, {"confirm_delete","मॉडल हटाएं?"}, {"dl_failed","डाउनलोड विफल:"}, {"downloading","डाउनलोड हो रहा है"}, {"size","आकार"}, {"on_disk","डिस्क पर"}, {"models_title","मॉडल प्रबंधन"}, {"model_status","स्थिति"}, {"priority_idle","निम्न"}, {"priority_below_normal","सामान्य से कम"}, {"priority_normal","सामान्य"}, {"priority_above_normal","सामान्य से अधिक"}, {"priority_high","उच्च"}, {"priority_realtime","रीयल-टाइम"} };

        t["pl"] = new Dictionary<string, string> { {"launch","Uruchom"}, {"models","Modele"}, {"exit","Wyjdź"}, {"download","Pobierz"}, {"verify","Sprawdź"}, {"delete","Usuń"}, {"cancel","Anuluj"}, {"not_installed","Nie zainstalowano"}, {"installed","Zainstalowano"}, {"incomplete","Niekompletne"}, {"preparing","Przygotowywanie..."}, {"all_ok","Wszystkie pliki OK!"}, {"files_bad","plików brakuje lub uszkodzonych"}, {"confirm_delete","Usunąć model?"}, {"dl_failed","Błąd pobierania:"}, {"downloading","Pobieranie"}, {"size","Rozmiar"}, {"on_disk","na dysku"}, {"models_title","Zarządzanie modelami"}, {"model_status","Status"}, {"priority_idle","Niski"}, {"priority_below_normal","Poniżej normalnego"}, {"priority_normal","Normalny"}, {"priority_above_normal","Powyżej normalnego"}, {"priority_high","Wysoki"}, {"priority_realtime","Czas rzeczywisty"} };

        t["nl"] = new Dictionary<string, string> { {"launch","Starten"}, {"models","Modellen"}, {"exit","Afsluiten"}, {"download","Downloaden"}, {"verify","Controleren"}, {"delete","Verwijderen"}, {"cancel","Annuleren"}, {"not_installed","Niet geïnstalleerd"}, {"installed","Geïnstalleerd"}, {"incomplete","Onvolledig"}, {"preparing","Voorbereiden..."}, {"all_ok","Alle bestanden OK!"}, {"files_bad","bestanden ontbreken of beschadigd"}, {"confirm_delete","Model verwijderen?"}, {"dl_failed","Download mislukt:"}, {"downloading","Downloaden"}, {"size","Grootte"}, {"on_disk","op schijf"}, {"models_title","Modelbeheer"}, {"model_status","Status"}, {"priority_idle","Laag"}, {"priority_below_normal","Onder normaal"}, {"priority_normal","Normaal"}, {"priority_above_normal","Boven normaal"}, {"priority_high","Hoog"}, {"priority_realtime","Realtime"} };

        t["it"] = new Dictionary<string, string> { {"launch","Avvia"}, {"models","Modelli"}, {"exit","Esci"}, {"download","Scarica"}, {"verify","Verifica"}, {"delete","Elimina"}, {"cancel","Annulla"}, {"not_installed","Non installato"}, {"installed","Installato"}, {"incomplete","Incompleto"}, {"preparing","Preparazione..."}, {"all_ok","Tutti i file OK!"}, {"files_bad","file mancanti o danneggiati"}, {"confirm_delete","Eliminare il modello?"}, {"dl_failed","Download fallito:"}, {"downloading","Download in corso"}, {"size","Dimensione"}, {"on_disk","su disco"}, {"models_title","Gestione modelli"}, {"model_status","Stato"}, {"priority_idle","Basso"}, {"priority_below_normal","Sotto la norma"}, {"priority_normal","Normale"}, {"priority_above_normal","Sopra la norma"}, {"priority_high","Alto"}, {"priority_realtime","Tempo reale"} };

        t["sv"] = new Dictionary<string, string> { {"launch","Starta"}, {"models","Modeller"}, {"exit","Avsluta"}, {"download","Ladda ner"}, {"verify","Verifiera"}, {"delete","Ta bort"}, {"cancel","Avbryt"}, {"not_installed","Inte installerad"}, {"installed","Installerad"}, {"incomplete","Ofullständig"}, {"preparing","Förbereder..."}, {"all_ok","Alla filer OK!"}, {"files_bad","filer saknas eller skadade"}, {"confirm_delete","Ta bort modell?"}, {"dl_failed","Nedladdning misslyckades:"}, {"downloading","Laddar ner"}, {"size","Storlek"}, {"on_disk","på disk"}, {"models_title","Modellhantering"}, {"model_status","Status"}, {"priority_idle","Låg"}, {"priority_below_normal","Under normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Över normal"}, {"priority_high","Hög"}, {"priority_realtime","Realtid"} };

        t["da"] = new Dictionary<string, string> { {"launch","Start"}, {"models","Modeller"}, {"exit","Afslut"}, {"download","Download"}, {"verify","Bekræft"}, {"delete","Slet"}, {"cancel","Annuller"}, {"not_installed","Ikke installeret"}, {"installed","Installeret"}, {"incomplete","Ufuldstændig"}, {"preparing","Forbereder..."}, {"all_ok","Alle filer OK!"}, {"files_bad","filer mangler eller beskadigede"}, {"confirm_delete","Slette model?"}, {"dl_failed","Download fejlede:"}, {"downloading","Downloader"}, {"size","Størrelse"}, {"on_disk","på disk"}, {"models_title","Modeladministration"}, {"model_status","Status"}, {"priority_idle","Lav"}, {"priority_below_normal","Under normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Over normal"}, {"priority_high","Høj"}, {"priority_realtime","Realtid"} };

        t["fi"] = new Dictionary<string, string> { {"launch","Käynnistä"}, {"models","Mallit"}, {"exit","Lopeta"}, {"download","Lataa"}, {"verify","Vahvista"}, {"delete","Poista"}, {"cancel","Peruuta"}, {"not_installed","Ei asennettu"}, {"installed","Asennettu"}, {"incomplete","Epätäydellinen"}, {"preparing","Valmistellaan..."}, {"all_ok","Kaikki tiedostot OK!"}, {"files_bad","tiedostot puuttuvat tai vaurioituneita"}, {"confirm_delete","Poista malli?"}, {"dl_failed","Lataus epäonnistui:"}, {"downloading","Ladataan"}, {"size","Koko"}, {"on_disk","levyllä"}, {"models_title","Hallinta"}, {"model_status","Tila"}, {"priority_idle","Matala"}, {"priority_below_normal","Normaalia alempi"}, {"priority_normal","Normaali"}, {"priority_above_normal","Normaalia korkeampi"}, {"priority_high","Korkea"}, {"priority_realtime","Reaaliaikainen"} };

        t["no"] = new Dictionary<string, string> { {"launch","Start"}, {"models","Modeller"}, {"exit","Avslutt"}, {"download","Last ned"}, {"verify","Bekreft"}, {"delete","Slett"}, {"cancel","Avbryt"}, {"not_installed","Ikke installert"}, {"installed","Installert"}, {"incomplete","Ufullstendig"}, {"preparing","Forbereder..."}, {"all_ok","Alle filer OK!"}, {"files_bad","filer mangler eller skadet"}, {"confirm_delete","Slette modell?"}, {"dl_failed","Nedlasting feilet:"}, {"downloading","Laster ned"}, {"size","Størrelse"}, {"on_disk","på disk"}, {"models_title","Modelladministrasjon"}, {"model_status","Status"}, {"priority_idle","Lav"}, {"priority_below_normal","Under normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Over normal"}, {"priority_high","Høy"}, {"priority_realtime","Sanntid"} };

        t["cs"] = new Dictionary<string, string> { {"launch","Spustit"}, {"models","Modely"}, {"exit","Konec"}, {"download","Stáhnout"}, {"verify","Ověřit"}, {"delete","Smazat"}, {"cancel","Zrušit"}, {"not_installed","Nenainstalováno"}, {"installed","Nainstalováno"}, {"incomplete","Neúplné"}, {"preparing","Příprava..."}, {"all_ok","Všechny soubory OK!"}, {"files_bad","soubory chybí nebo poškozené"}, {"confirm_delete","Smazat model?"}, {"dl_failed","Stahování selhalo:"}, {"downloading","Stahování"}, {"size","Velikost"}, {"on_disk","na disku"}, {"models_title","Správa modelů"}, {"model_status","Stav"}, {"priority_idle","Nízká"}, {"priority_below_normal","Pod normální"}, {"priority_normal","Normální"}, {"priority_above_normal","Nad normální"}, {"priority_high","Vysoká"}, {"priority_realtime","Reálný čas"} };

        t["sk"] = new Dictionary<string, string> { {"launch","Spustiť"}, {"models","Modely"}, {"exit","Koniec"}, {"download","Stiahnuť"}, {"verify","Overiť"}, {"delete","Vymazať"}, {"cancel","Zrušiť"}, {"not_installed","Nenainštalované"}, {"installed","Nainštalované"}, {"incomplete","Neúplné"}, {"preparing","Príprava..."}, {"all_ok","Všetky súbory OK!"}, {"files_bad","súbory chýbajú alebo poškodené"}, {"confirm_delete","Vymazať model?"}, {"dl_failed","Sťahovanie zlyhalo:"}, {"downloading","Sťahovanie"}, {"size","Veľkosť"}, {"on_disk","na disku"}, {"models_title","Správa modelov"}, {"model_status","Stav"}, {"priority_idle","Nízka"}, {"priority_below_normal","Pod normálnu"}, {"priority_normal","Normálna"}, {"priority_above_normal","Nad normálnu"}, {"priority_high","Vysoká"}, {"priority_realtime","Reálny čas"} };

        t["hu"] = new Dictionary<string, string> { {"launch","Indítás"}, {"models","Modellek"}, {"exit","Kilépés"}, {"download","Letöltés"}, {"verify","Ellenőrzés"}, {"delete","Törlés"}, {"cancel","Mégse"}, {"not_installed","Nincs telepítve"}, {"installed","Telepítve"}, {"incomplete","Hiányos"}, {"preparing","Előkészítés..."}, {"all_ok","Minden fájl OK!"}, {"files_bad","fájlok hiányoznak vagy sérültek"}, {"confirm_delete","Modell törlése?"}, {"dl_failed","Letöltés sikertelen:"}, {"downloading","Letöltés folyamatban"}, {"size","Méret"}, {"on_disk","a lemezen"}, {"models_title","Modellkezelés"}, {"model_status","Állapot"}, {"priority_idle","Alacsony"}, {"priority_below_normal","Normál alatti"}, {"priority_normal","Normál"}, {"priority_above_normal","Normál feletti"}, {"priority_high","Magas"}, {"priority_realtime","Valós idejű"} };

        t["ro"] = new Dictionary<string, string> { {"launch","Lansează"}, {"models","Modele"}, {"exit","Ieșire"}, {"download","Descarcă"}, {"verify","Verifică"}, {"delete","Șterge"}, {"cancel","Anulează"}, {"not_installed","Neinstalat"}, {"installed","Instalat"}, {"incomplete","Incomplet"}, {"preparing","Se pregătește..."}, {"all_ok","Toate fișierele OK!"}, {"files_bad","fișiere lipsă sau corupte"}, {"confirm_delete","Șterge modelul?"}, {"dl_failed","Descărcare eșuată:"}, {"downloading","Se descarcă"}, {"size","Dimensiune"}, {"on_disk","pe disc"}, {"models_title","Gestionare modele"}, {"model_status","Stare"}, {"priority_idle","Scăzut"}, {"priority_below_normal","Sub normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Peste normal"}, {"priority_high","Ridicat"}, {"priority_realtime","Timp real"} };

        t["bg"] = new Dictionary<string, string> { {"launch","Стартирай"}, {"models","Модели"}, {"exit","Изход"}, {"download","Изтегли"}, {"verify","Провери"}, {"delete","Изтрий"}, {"cancel","Отказ"}, {"not_installed","Не е инсталиран"}, {"installed","Инсталиран"}, {"incomplete","Непълен"}, {"preparing","Подготовка..."}, {"all_ok","Всички файлове са OK!"}, {"files_bad","файлове липсват или са повредени"}, {"confirm_delete","Изтрий модела?"}, {"dl_failed","Грешка при изтегляне:"}, {"downloading","Изтегляне"}, {"size","Размер"}, {"on_disk","на диска"}, {"models_title","Управление на модели"}, {"model_status","Статус"}, {"priority_idle","Нисък"}, {"priority_below_normal","Под нормално"}, {"priority_normal","Нормален"}, {"priority_above_normal","Над нормално"}, {"priority_high","Висок"}, {"priority_realtime","Реално време"} };

        t["el"] = new Dictionary<string, string> { {"launch","Εκκίνηση"}, {"models","Μοντέλα"}, {"exit","Έξοδος"}, {"download","Λήψη"}, {"verify","Επαλήθευση"}, {"delete","Διαγραφή"}, {"cancel","Ακύρωση"}, {"not_installed","Δεν εγκαταστάθηκε"}, {"installed","Εγκατεστημένο"}, {"incomplete","Ελλιπές"}, {"preparing","Προετοιμασία..."}, {"all_ok","Όλα τα αρχεία OK!"}, {"files_bad","αρχεία λείπουν ή κατεστραμμένα"}, {"confirm_delete","Διαγραφή μοντέλου;"}, {"dl_failed","Αποτυχία λήψης:"}, {"downloading","Λήψη"}, {"size","Μέγεθος"}, {"on_disk","στο δίσκο"}, {"models_title","Διαχείριση μοντέλων"}, {"model_status","Κατάσταση"}, {"priority_idle","Χαμηλό"}, {"priority_below_normal","Κάτω από το κανονικό"}, {"priority_normal","Κανονικό"}, {"priority_above_normal","Πάνω από το κανονικό"}, {"priority_high","Υψηλό"}, {"priority_realtime","Πραγματικό χρόνο"} };

        t["th"] = new Dictionary<string, string> { {"launch","เริ่ม"}, {"models","โมเดล"}, {"exit","ออก"}, {"download","ดาวน์โหลด"}, {"verify","ตรวจสอบ"}, {"delete","ลบ"}, {"cancel","ยกเลิก"}, {"not_installed","ยังไม่ได้ติดตั้ง"}, {"installed","ติดตั้งแล้ว"}, {"incomplete","ไม่สมบูรณ์"}, {"preparing","กำลังเตรียม..."}, {"all_ok","ไฟล์ทั้งหมด OK!"}, {"files_bad","ไฟล์หายไปหรือเสียหาย"}, {"confirm_delete","ลบโมเดล?"}, {"dl_failed","ดาวน์โหลดล้มเหลว:"}, {"downloading","กำลังดาวน์โหลด"}, {"size","ขนาด"}, {"on_disk","บนดิสก์"}, {"models_title","จัดการโมเดล"}, {"model_status","สถานะ"}, {"priority_idle","ต่ำ"}, {"priority_below_normal","ต่ำกว่าปกติ"}, {"priority_normal","ปกติ"}, {"priority_above_normal","สูงกว่าปกติ"}, {"priority_high","สูง"}, {"priority_realtime","เรียลไทม์"} };

        t["id"] = new Dictionary<string, string> { {"launch","Jalankan"}, {"models","Model"}, {"exit","Keluar"}, {"download","Unduh"}, {"verify","Verifikasi"}, {"delete","Hapus"}, {"cancel","Batal"}, {"not_installed","Belum terinstal"}, {"installed","Terinstal"}, {"incomplete","Tidak lengkap"}, {"preparing","Mempersiapkan..."}, {"all_ok","Semua file OK!"}, {"files_bad","file hilang atau rusak"}, {"confirm_delete","Hapus model?"}, {"dl_failed","Gagal mengunduh:"}, {"downloading","Mengunduh"}, {"size","Ukuran"}, {"on_disk","di disk"}, {"models_title","Manajemen model"}, {"model_status","Status"}, {"priority_idle","Rendah"}, {"priority_below_normal","Di bawah normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Di atas normal"}, {"priority_high","Tinggi"}, {"priority_realtime","Waktu nyata"} };

        t["ms"] = new Dictionary<string, string> { {"launch","Lancar"}, {"models","Model"}, {"exit","Keluar"}, {"download","Muat turun"}, {"verify","Sahkan"}, {"delete","Padam"}, {"cancel","Batal"}, {"not_installed","Belum dipasang"}, {"installed","Dipasang"}, {"incomplete","Tidak lengkap"}, {"preparing","Menyediakan..."}, {"all_ok","Semua fail OK!"}, {"files_bad","fail tiada atau rosak"}, {"confirm_delete","Padam model?"}, {"dl_failed","Muat turun gagal:"}, {"downloading","Memuat turun"}, {"size","Saiz"}, {"on_disk","pada cakera"}, {"models_title","Pengurusan model"}, {"model_status","Status"}, {"priority_idle","Rendah"}, {"priority_below_normal","Di bawah normal"}, {"priority_normal","Normal"}, {"priority_above_normal","Di atas normal"}, {"priority_high","Tinggi"}, {"priority_realtime","Masa nyata"} };

        t["ca"] = new Dictionary<string, string> { {"launch","Iniciar"}, {"models","Models"}, {"exit","Sortir"}, {"download","Descarregar"}, {"verify","Verificar"}, {"delete","Eliminar"}, {"cancel","Cancel·lar"}, {"not_installed","No instal·lat"}, {"installed","Instal·lat"}, {"incomplete","Incomplet"}, {"preparing","Preparant..."}, {"all_ok","Tots els fitxers OK!"}, {"files_bad","fitxers mancats o danyats"}, {"confirm_delete","Eliminar el model?"}, {"dl_failed","Error de descàrrega:"}, {"downloading","Descarregant"}, {"size","Mida"}, {"on_disk","al disc"}, {"models_title","Gestió de models"}, {"model_status","Estat"}, {"priority_idle","Baix"}, {"priority_below_normal","Per sota de la normalitat"}, {"priority_normal","Normal"}, {"priority_above_normal","Per sobre de la normalitat"}, {"priority_high","Alt"}, {"priority_realtime","Temps real"} };

        t["et"] = new Dictionary<string, string> { {"launch","Käivita"}, {"models","Mudelid"}, {"exit","Välju"}, {"download","Laadi alla"}, {"verify","Kontrolli"}, {"delete","Kustuta"}, {"cancel","Tühista"}, {"not_installed","Paigaldamata"}, {"installed","Paigaldatud"}, {"incomplete","Puudulik"}, {"preparing","Ettevalmistus..."}, {"all_ok","Kõik failid OK!"}, {"files_bad","failid puuduvad või vigased"}, {"confirm_delete","Kustuta mudel?"}, {"dl_failed","Allalaadimine ebaõnnestus:"}, {"downloading","Allalaadimine"}, {"size","Suurus"}, {"on_disk","kettal"}, {"models_title","Mudelite haldus"}, {"model_status","Olek"}, {"priority_idle","Madalam"}, {"priority_below_normal","Alla normaalse"}, {"priority_normal","Normaalne"}, {"priority_above_normal","Üle normaalse"}, {"priority_high","Kõrge"}, {"priority_realtime","Reaalajas"} };

        t["lv"] = new Dictionary<string, string> { {"launch","Palaist"}, {"models","Modeļi"}, {"exit","Iziet"}, {"download","Lejupielādēt"}, {"verify","Pārbaudīt"}, {"delete","Dzēst"}, {"cancel","Atcelt"}, {"not_installed","Nav instalēts"}, {"installed","Instalēts"}, {"incomplete","Nepilnīgs"}, {"preparing","Sagatavošana..."}, {"all_ok","Visi faili OK!"}, {"files_bad","faili trūkst vai bojāti"}, {"confirm_delete","Dzēst modeli?"}, {"dl_failed","Lejupielāde neizdevās:"}, {"downloading","Lejupielāde"}, {"size","Izmērs"}, {"on_disk","uz diska"}, {"models_title","Modeļu pārvaldība"}, {"model_status","Statuss"}, {"priority_idle","Zems"}, {"priority_below_normal","Zem normālā"}, {"priority_normal","Normāls"}, {"priority_above_normal","Virs normālā"}, {"priority_high","Augsts"}, {"priority_realtime","Reāllaikā"} };

        t["lt"] = new Dictionary<string, string> { {"launch","Paleisti"}, {"models","Modeliai"}, {"exit","Išeiti"}, {"download","Atsisiųsti"}, {"verify","Patikrinti"}, {"delete","Ištrinti"}, {"cancel","Atšaukti"}, {"not_installed","Neįdiegta"}, {"installed","Įdiegta"}, {"incomplete","Nebaigtas"}, {"preparing","Ruošiama..."}, {"all_ok","Visi failai OK!"}, {"files_bad","failai trūksta arba sugadinti"}, {"confirm_delete","Ištrinti modelį?"}, {"dl_failed","Atsisiuntimas nepavyko:"}, {"downloading","Atsisiunčiama"}, {"size","Dydis"}, {"on_disk","diske"}, {"models_title","Modelių valdymas"}, {"model_status","Būsena"}, {"priority_idle","Žemas"}, {"priority_below_normal","Žemiau normalaus"}, {"priority_normal","Normalus"}, {"priority_above_normal","Aukščiau normalaus"}, {"priority_high","Aukštas"}, {"priority_realtime","Realiuoju laiku"} };

        t["sl"] = new Dictionary<string, string> { {"launch","Zaženi"}, {"models","Modeli"}, {"exit","Izhod"}, {"download","Prenesi"}, {"verify","Preveri"}, {"delete","Izbriši"}, {"cancel","Prekliči"}, {"not_installed","Ni nameščeno"}, {"installed","Nameščeno"}, {"incomplete","Nepopolno"}, {"preparing","Pripravljam..."}, {"all_ok","Vse datoteke OK!"}, {"files_bad","datoteke manjkajo ali poškodovane"}, {"confirm_delete","Izbrisati model?"}, {"dl_failed","Prenos ni uspel:"}, {"downloading","Prenašanje"}, {"size","Velikost"}, {"on_disk","na disku"}, {"models_title","Upravljanje modelov"}, {"model_status","Stanje"}, {"priority_idle","Nizka"}, {"priority_below_normal","Pod normalno"}, {"priority_normal","Normalna"}, {"priority_above_normal","Nad normalno"}, {"priority_high","Visoka"}, {"priority_realtime","V resničnem času"} };

        _translationsCache = t;
        return t;
    }

    static string T(string key)
    {
        var tr = BuildTranslations();
        if (Lang != null && tr.ContainsKey(Lang) && tr[Lang].ContainsKey(key))
            return tr[Lang][key];
        switch (key)
        {
            case "launch": return "Launch";
            case "models": return "Models";
            case "settings": return "Settings";
            case "exit": return "Exit";
            case "download": return "Download";
            case "verify": return "Verify";
            case "delete": return "Delete";
            case "cancel": return "Cancel";
            case "src_hf": return "HuggingFace";
            case "src_mirror": return "HF Mirror";
            case "src_ms": return "ModelScope";
            case "not_installed": return "Not installed";
            case "installed": return "Installed";
            case "incomplete": return "Incomplete";
            case "preparing": return "Preparing...";
            case "all_ok": return "All files OK!";
            case "files_bad": return "files missing or corrupted";
            case "confirm_delete": return "Delete model?";
            case "dl_failed": return "Download failed:";
            case "downloading": return "Downloading";
            case "size": return "Size";
            case "on_disk": return "on disk";
            case "models_title": return "Model Management";
            case "model_status": return "Status";
            case "file_damaged": return "file is damaged";
            case "file_incomplete": return "file is incomplete (interrupted download)";
            case "file_not_found": return "file not found";
            case "files_ok": return "files OK";
            case "files_with_problems": return "files with problems";
            case "redownload_question": return "Re-download these files?";
            case "file_0": return "Audio processor";
            case "file_1": return "Decoder (main, ~750 MB)";
            case "file_2": return "Encoder";
            case "file_3": return "Token merge vocabulary";
            case "file_4": return "Tokenizer settings";
            case "file_5": return "Token vocabulary";
            case "settings_title": return "Settings Management";
            case "settings_backup": return "Backup";
            case "settings_restore": return "Restore";
            case "settings_delete": return "Delete settings";
            case "settings_info": return "Settings file:";
            case "settings_no_file": return "Settings file not found";
            case "settings_backup_ok": return "Backup saved";
            case "settings_restore_ok": return "Settings restored";
            case "settings_delete_confirm": return "Delete all app settings?\nThis cannot be undone.";
            case "settings_delete_ok": return "Settings deleted";
            case "performance": return "Performance";
            case "perf_title": return "Performance";
            case "perf_tab_priority": return "Priority";
            case "perf_tab_cores": return "Cores";
            case "perf_tab_gpu": return "GPU";
            case "perf_gpu_device": return "GPU device for DirectML";
            case "perf_gpu_auto": return "Auto (default GPU)";
            case "perf_gpu_hint": return "Select which GPU to use for AI inference.\nRequires restart to take effect.";
            case "perf_vulkan_device": return "Vulkan GPU";
            case "perf_vulkan_hint": return "Select Vulkan GPU for GGUF models (transcribe.cpp).";
            case "perf_gpu_restart_notice": return "GPU setting changed. The launcher will restart to apply.";
            case "perf_priority": return "Process priority";
            case "perf_priority_desc": return "Sets priority for the entire process tree.\nAbove Normal and higher- for maximum responsiveness.\nBelow Normal- if the app should not interfere with other tasks.";
            case "perf_pcores": return "Use P-cores only (performance)";
            case "perf_pcores_hint": return "Author recommends P-cores for best performance.\nE-cores may cause audio processing delays.";
            case "perf_save": return "Save";
            case "perf_saved": return "Performance settings saved";
            case "priority_idle": return "Low";
            case "priority_below_normal": return "Below Normal";
            case "priority_normal": return "Normal";
            case "priority_above_normal": return "Above Normal";
            case "priority_high": return "High";
            case "priority_realtime": return "Real Time";
            default: return key;
        }
    }

    static void DetectLanguage()
    {
        try
        {
            string name = CultureInfo.CurrentUICulture.Name.ToLowerInvariant();
            string two = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            var tr = BuildTranslations();
            if (tr.ContainsKey(name)) Lang = name;
            else if (tr.ContainsKey(two)) Lang = two;
            else if (name.StartsWith("zh")) Lang = "zh";
            else if (name.StartsWith("pt")) Lang = "pt";
            else Lang = two;
        }
        catch { Lang = "en"; }
    }

    // --- Model helpers ---
    enum ModelStatus { Missing, Incomplete, Ready }

    static void EnsureInstalledManifest(int mi)
    {
        string dir = Path.Combine(ModelsDir, ModelInstallDir(mi));
        string manifestPath = Path.Combine(dir, "installed-manifest.json");
        if (File.Exists(manifestPath)) return;
        try
        {
            string json = "{\n"
                + "  \"manifest_version\": 1,\n"
                + "  \"model_id\": \"" + MODELS[mi][0] + "\",\n"
                + "  \"engine\": \"sherpa-onnx\",\n"
                + "  \"install_dirname\": \"" + ModelInstallDir(mi) + "\",\n"
                + "  \"selected_source\": \"huggingface\",\n"
                + "  \"selected_revision\": \"" + REVISIONS[mi] + "\"\n"
                + "}";
            File.WriteAllText(manifestPath, json);
        }
        catch { }
    }

    static long ModelTotalSize(int mi)
    {
        long t = 0;
        foreach (long s in SIZES[mi]) t += s;
        return t;
    }

    static ModelStatus CheckModel(int mi, out int foundFiles, out long foundSize)
    {
        foundFiles = 0;
        foundSize = 0;
        bool hasSizeMismatch = false;
        string dir = Path.Combine(ModelsDir, ModelInstallDir(mi));
        if (!Directory.Exists(dir)) return ModelStatus.Missing;
        for (int i = 0; i < FILES[mi].Length; i++)
        {
            string fp = Path.Combine(dir, LOCAL_FILES[mi][i]);
            if (File.Exists(fp))
            {
                try {
                    long actual = new FileInfo(fp).Length;
                    foundSize += actual;
                    if (SIZES[mi][i] > 0 && actual != SIZES[mi][i])
                        hasSizeMismatch = true;
                }
                catch { foundSize += SIZES[mi][i]; }
                foundFiles++;
            }
        }
        if (hasSizeMismatch) return ModelStatus.Incomplete;
        if (foundFiles == FILES[mi].Length) return ModelStatus.Ready;
        if (foundFiles > 0) return ModelStatus.Incomplete;
        return ModelStatus.Missing;
    }

    static string ReadAppVersion(string appSrc)
    {
        try
        {
            string initPath = Path.Combine(appSrc, "puripuly_heart", "__init__.py");
            if (File.Exists(initPath))
            {
                foreach (string line in File.ReadAllLines(initPath))
                {
                    if (line.StartsWith("__version__"))
                    {
                        int q1 = line.IndexOf('"');
                        int q2 = line.IndexOf('"', q1 + 1);
                        if (q1 >= 0 && q2 > q1) return line.Substring(q1 + 1, q2 - q1 - 1);
                        q1 = line.IndexOf('\'');
                        q2 = line.IndexOf('\'', q1 + 1);
                        if (q1 >= 0 && q2 > q1) return line.Substring(q1 + 1, q2 - q1 - 1);
                    }
                }
            }
        }
        catch { }
        return "?.?.?";
    }

    static string FormatSize(long bytes)
    {
        if (bytes >= 1073741824L) return string.Format("{0:F1} GB", bytes / 1073741824.0);
        if (bytes >= 1048576L) return string.Format("{0:F0} MB", bytes / 1048576.0);
        return string.Format("{0:F0} KB", bytes / 1024.0);
    }

    // --- Main ---
    static int Main(string[] args)
    {
        DetectLanguage();
        InitColors(IsDarkTheme());
        ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072 | SecurityProtocolType.Tls11 | SecurityProtocolType.Tls;

        string root = AppDomain.CurrentDomain.BaseDirectory;
        string dataDir = Path.Combine(root, "data");
        ModelsDir = Path.Combine(dataDir, "models");
        string pythonExe = Path.Combine(root, "python", "python.exe");
        string appSrc = Path.Combine(root, "app", "src");
        string mainPy = Path.Combine(appSrc, "puripuly_heart", "main.py");

        var perfSettings = LoadPerformanceSettings(dataDir);
        SetupKillOnCloseWithPerf(perfSettings);
        var gpuList = DetectGpus();
        var vulkanDevices = DetectVulkanDevices();

        // Resolve GPU index from LUID if available, else use legacy index
        int resolvedGpuIdx = !string.IsNullOrEmpty(perfSettings.GpuLuid)
            ? FindGpuIndexByLuid(gpuList, perfSettings.GpuLuid)
            : (perfSettings.GpuDevice >= 0 && perfSettings.GpuDevice < gpuList.Count ? perfSettings.GpuDevice : -1);
        if (resolvedGpuIdx < 0) resolvedGpuIdx = 0;

        SetGpuPreferenceRegistry(resolvedGpuIdx, gpuList, dataDir);
        string gpuLabel = resolvedGpuIdx >= 0 && resolvedGpuIdx < gpuList.Count
            ? gpuList[resolvedGpuIdx].Name
            : "GPU not found";

        if (!File.Exists(pythonExe))
        {
            MessageBox.Show("python.exe not found in:\n" + Path.Combine(root, "python"),
                "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (!File.Exists(mainPy))
        {
            MessageBox.Show("main.py not found in:\n" + Path.Combine(appSrc, "puripuly_heart"),
                "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        Directory.CreateDirectory(dataDir);
        // Always create model directories
        for (int mi = 0; mi < MODELS.Length; mi++)
            Directory.CreateDirectory(Path.Combine(ModelsDir, ModelInstallDir(mi)));

        // Read app version
        string appVersion = ReadAppVersion(appSrc);
        const string launcherVersion = "1.0.0";

        // --- Main launcher window ---
        string launchMode = null; // null=no launch, "gpu" or "cpu"

        var form = new Form
        {
            Text = "PuriPuly Heart GPU",
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterScreen,
            Size = new Size(400, 350),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        var titleLabel = new Label
        {
            Text = "\u2665  PuriPuly Heart",
            Font = new Font("Segoe UI", 18, FontStyle.Bold),
            ForeColor = ACCENT_PINK,
            AutoSize = false,
            Size = new Size(380, 36),
            Location = new Point(0, 10),
            TextAlign = ContentAlignment.MiddleCenter,
        };

        var versionLabel = new Label
        {
            Text = "app v" + appVersion + "  \u00B7  launcher v" + launcherVersion,
            Font = new Font("Segoe UI", 9),
            ForeColor = FG3,
            AutoSize = false,
            Size = new Size(380, 18),
            Location = new Point(0, 46),
            TextAlign = ContentAlignment.MiddleCenter,
        };

        var sep = new Label { AutoSize = false, Size = new Size(360, 1), Location = new Point(20, 70), BackColor = FG3 };

        var gpuInfoLabel = new Label
        {
            Text = "GPU: " + gpuLabel,
            Font = new Font("Segoe UI", 9),
            ForeColor = FG3,
            AutoSize = false,
            Size = new Size(380, 18),
            Location = new Point(0, 72),
            TextAlign = ContentAlignment.MiddleCenter,
        };

        int btnW = 360, btnH = 38, btnX = 20, btnY = 92, btnGap = 6;

        var btnLaunch = new Button
        {
            Text = "\u25B6  " + T("launch"),
            Font = new Font("Segoe UI", 13, FontStyle.Bold),
            Size = new Size(btnW, 44),
            Location = new Point(btnX, btnY),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_GREEN,
            TextAlign = ContentAlignment.MiddleCenter,
        };
        btnLaunch.FlatAppearance.BorderColor = ACCENT_GREEN;

        var btnPerf = new Button
        {
            Text = "\u26A1  " + T("performance"),
            Font = new Font("Segoe UI", 11),
            Size = new Size(btnW, btnH),
            Location = new Point(btnX, btnY + 50),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = Color.FromArgb(255, 180, 80),
            TextAlign = ContentAlignment.MiddleLeft,
        };
        btnPerf.FlatAppearance.BorderColor = Color.FromArgb(255, 180, 80);

        var btnModels = new Button
        {
            Text = "\u2699  " + T("models"),
            Font = new Font("Segoe UI", 11),
            Size = new Size(btnW, btnH),
            Location = new Point(btnX, btnY + 50 + btnH + btnGap),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_BLUE,
            TextAlign = ContentAlignment.MiddleLeft,
        };
        btnModels.FlatAppearance.BorderColor = ACCENT_BLUE;

        var btnSettings = new Button
        {
            Text = "\u2692  " + T("settings"),
            Font = new Font("Segoe UI", 11),
            Size = new Size(btnW, btnH),
            Location = new Point(btnX, btnY + 50 + (btnH + btnGap) * 2),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_YELLOW,
            TextAlign = ContentAlignment.MiddleLeft,
        };
        btnSettings.FlatAppearance.BorderColor = ACCENT_YELLOW;

        var btnExit = new Button
        {
            Text = T("exit"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(btnW, 30),
            Location = new Point(btnX, btnY + 50 + (btnH + btnGap) * 3 + 6),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = FG3,
        };
        btnExit.FlatAppearance.BorderColor = FG3;

        btnLaunch.Click += (s, e) => { launchMode = "gpu"; form.Close(); };
        btnModels.Click += (s, e) => { ShowModelsDialog(); };
        btnSettings.Click += (s, e) => { ShowSettingsDialog(dataDir); };
        btnPerf.Click += (s, e) =>
        {
            var result = ShowPerformanceDialog(dataDir);
            var ps = LoadPerformanceSettings(dataDir);
            var gl = DetectGpus();
            int rIdx = !string.IsNullOrEmpty(ps.GpuLuid) ? FindGpuIndexByLuid(gl, ps.GpuLuid)
                : (ps.GpuDevice >= 0 && ps.GpuDevice < gl.Count ? ps.GpuDevice : -1);
            if (rIdx < 0) rIdx = 0;
            SetGpuPreferenceRegistry(rIdx, gl, dataDir);
            gpuInfoLabel.Text = "GPU: " + (rIdx >= 0 && rIdx < gl.Count ? gl[rIdx].Name : "GPU not found");
            if (result == DialogResult.Retry)
            {
                form.Close();
                var selfExe = System.Diagnostics.Process.GetCurrentProcess().MainModule.FileName;
                System.Diagnostics.Process.Start(selfExe);
                return;
            }
        };
        btnExit.Click += (s, e) => { form.Close(); };

        form.Controls.AddRange(new Control[] { titleLabel, versionLabel, sep, gpuInfoLabel, btnLaunch, btnModels, btnSettings, btnPerf, btnExit });
        form.ShowDialog();

        if (launchMode == null) return 0;

        // Ensure installed-manifest.json for verified models
        Form verifySplash = null;
        Label verifyLabel = null;
        ProgressBar verifyBar = null;
        bool needsVerify = false;
        for (int i = 0; i < MODELS.Length; i++)
        {
            string mp = Path.Combine(ModelsDir, ModelInstallDir(i), "installed-manifest.json");
            if (!File.Exists(mp) && Directory.Exists(Path.Combine(ModelsDir, ModelInstallDir(i))))
            { needsVerify = true; break; }
        }
        if (needsVerify)
        {
            verifySplash = new Form { Text = "PuriPuly Heart GPU", FormBorderStyle = FormBorderStyle.FixedSingle, StartPosition = FormStartPosition.CenterScreen, Size = new Size(400, 140), BackColor = BG, TopMost = true, ShowInTaskbar = false, ControlBox = false };
            verifyLabel = new Label { Text = "", Font = new Font("Segoe UI", 10), ForeColor = FG, AutoSize = false, Size = new Size(370, 22), Location = new Point(12, 15) };
            verifyBar = new ProgressBar { Location = new Point(12, 45), Size = new Size(370, 22), Style = ProgressBarStyle.Continuous };
            verifySplash.Controls.Add(verifyLabel);
            verifySplash.Controls.Add(verifyBar);
            verifySplash.Show();
            verifySplash.Refresh();
        }
        for (int i = 0; i < MODELS.Length; i++)
        {
            string manifestPath = Path.Combine(ModelsDir, ModelInstallDir(i), "installed-manifest.json");
            if (File.Exists(manifestPath)) continue;
            string dir = Path.Combine(ModelsDir, ModelInstallDir(i));
            if (!Directory.Exists(dir)) continue;
            bool allOk = true;
            for (int fi = 0; fi < FILES[i].Length; fi++)
            {
                string fp = Path.Combine(dir, LOCAL_FILES[i][fi]);
                if (!File.Exists(fp)) { allOk = false; break; }
                long sz = new FileInfo(fp).Length;
                if (sz != SIZES[i][fi]) { allOk = false; break; }
                if (verifyLabel != null) { verifyLabel.Text = MODELS[i][1] + " \u2014 " + LOCAL_FILES[i][fi]; verifyLabel.Refresh(); }
                if (verifyBar != null) { verifyBar.Value = (int)((long)(fi + 1) * 100 / FILES[i].Length); verifyBar.Refresh(); }
                Application.DoEvents();
                if (ComputeCRC32(fp) != CRC32S[i][fi]) { allOk = false; break; }
            }
            if (allOk) EnsureInstalledManifest(i);
        }
        if (verifySplash != null) { verifySplash.Close(); verifySplash.Dispose(); }

        // --- Launch app ---
        string exeName = Path.GetFileNameWithoutExtension(System.Reflection.Assembly.GetExecutingAssembly().Location);
        bool isDebug = exeName.ToLowerInvariant().Contains("debug");

        Form splash = null;
        if (!isDebug)
        {
            splash = new Form { Text = "PuriPuly Heart GPU", FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.CenterScreen, Size = new Size(320, 160), BackColor = BG, TopMost = true, ShowInTaskbar = false };
            splash.Controls.Add(new Label { Text = "PuriPuly Heart GPU", Font = new Font("Segoe UI", 16, FontStyle.Bold), ForeColor = ACCENT_PINK, AutoSize = false, Size = new Size(320, 40), Location = new Point(0, 30), TextAlign = ContentAlignment.MiddleCenter });
            splash.Controls.Add(new Label { Text = "Loading...", Font = new Font("Segoe UI", 10), ForeColor = FG2, AutoSize = false, Size = new Size(320, 25), Location = new Point(0, 85), TextAlign = ContentAlignment.MiddleCenter });
            splash.Show();
            Application.DoEvents();
        }

        var psi = new ProcessStartInfo { FileName = pythonExe, UseShellExecute = false, CreateNoWindow = !isDebug, WorkingDirectory = root };
        foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
            psi.Environment[(string)entry.Key] = (string)entry.Value;
        psi.Environment["PURIPULY_HEART_DATA_DIR"] = dataDir;
        psi.Environment["PURIPULY_MODE"] = launchMode;
        if (launchMode == "gpu")
        {
            int dxgiIdx = 0;
            int vulkanIdx = 0;
            string debugLog = "";
            if (!string.IsNullOrEmpty(perfSettings.GpuLuid))
            {
                int gpuIdx = FindGpuIndexByLuid(gpuList, perfSettings.GpuLuid);
                dxgiIdx = gpuIdx >= 0 ? gpuList[gpuIdx].DxgiIndex : 0;
                vulkanIdx = FindVulkanIndexByLuid(vulkanDevices, perfSettings.GpuLuid);
                if (vulkanIdx < 0) vulkanIdx = 0;
                debugLog = DateTime.Now.ToString("HH:mm:ss") + " LUID mode: LUID=" + perfSettings.GpuLuid
                    + " gpuIdx=" + gpuIdx + " dxgiIdx=" + dxgiIdx + " vulkanIdx=" + vulkanIdx
                    + " gpuList.Count=" + gpuList.Count + " vulkanDevices.Count=" + vulkanDevices.Count + "\n";
                for (int gi = 0; gi < gpuList.Count; gi++)
                    debugLog += "  DXGI[" + gi + "]=" + gpuList[gi].Name + " LUID=" + LuidToString(gpuList[gi].AdapterLuid) + " DxgiIndex=" + gpuList[gi].DxgiIndex + "\n";
                for (int vi = 0; vi < vulkanDevices.Count; vi++)
                    debugLog += "  VK[" + vulkanDevices[vi].Index + "]=" + vulkanDevices[vi].Name + " LUID=" + (vulkanDevices[vi].Luid ?? "null") + "\n";
            }
            else
            {
                dxgiIdx = perfSettings.GpuDevice >= 0 && perfSettings.GpuDevice < gpuList.Count
                    ? gpuList[perfSettings.GpuDevice].DxgiIndex : 0;
                vulkanIdx = perfSettings.VulkanDevice;
                debugLog = DateTime.Now.ToString("HH:mm:ss") + " Legacy mode: GpuDevice=" + perfSettings.GpuDevice + " dxgiIdx=" + dxgiIdx + " vulkanIdx=" + vulkanIdx + "\n";
            }
            debugLog += "  → SHERPA_GPU_DEVICE=" + dxgiIdx + " TRANSCRIBE_VULKAN_DEVICE=" + vulkanIdx + "\n";
            psi.Environment["SHERPA_GPU_DEVICE"] = dxgiIdx.ToString();
            psi.Environment["TRANSCRIBE_VULKAN_DEVICE"] = vulkanIdx.ToString();
            try { System.IO.File.AppendAllText(System.IO.Path.Combine(dataDir, "gpu_debug.log"), debugLog); } catch { }
        }

        string cmdArgs = "\"" + mainPy + "\"";
        foreach (string arg in args) cmdArgs += " \"" + arg + "\"";
        if (isDebug) cmdArgs += " \"--debug-ui-preview\"";
        psi.Arguments = cmdArgs;
        if (isDebug)
        {
            psi.RedirectStandardError = true;
            psi.StandardErrorEncoding = System.Text.Encoding.UTF8;
        }

        // Kill any leftover python/flet processes before launching with new GPU
        try
        {
            foreach (var p in Process.GetProcessesByName("python"))
            {
                try { p.Kill(); p.WaitForExit(2000); } catch { }
            }
            foreach (var p in Process.GetProcessesByName("flet"))
            {
                try { p.Kill(); p.WaitForExit(2000); } catch { }
            }
        }
        catch { }

        try
        {
            string debugStderr = null;
            int debugExitCode = 0;
            using (var proc = Process.Start(psi))
            {
                // Apply performance settings directly to Python process
                if (proc != null) {
                    try { proc.PriorityClass = (ProcessPriorityClass)PriorityToClass(perfSettings.Priority); } catch { }
                    if (perfSettings.PCoresOnly || perfSettings.NumaMode > 0) {
                        try {
                            var pcoreInfo = BuildPCoreAffinityMask(perfSettings);
                            if (pcoreInfo.Mask != 0) proc.ProcessorAffinity = (IntPtr)pcoreInfo.Mask;
                        } catch { }
                    }
                    if (isDebug)
                    {
                        var stderrReader = proc.StandardError.ReadToEndAsync();
                        proc.WaitForExit();
                        debugStderr = stderrReader.IsCompleted ? stderrReader.Result : "";
                        debugExitCode = proc.ExitCode;
                    }
                }
                if (!isDebug && splash != null) { for (int i = 0; i < 20; i++) { Thread.Sleep(100); Application.DoEvents(); } splash.Close(); splash.Dispose(); }
            }
            if (isDebug && debugExitCode != 0 && debugStderr != null && debugStderr.Trim().Length > 0)
            {
                MessageBox.Show(debugStderr, "PuriPuly Heart — Python stderr (exit " + debugExitCode + ")", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            return 0;
        }
        catch (Exception ex)
        {
            if (splash != null) { splash.Close(); splash.Dispose(); }
            MessageBox.Show("Failed to start:\n" + ex.Message, "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    // --- Settings dialog ---
    static void ShowSettingsDialog(string dataDir)
    {
        string settingsPath = Path.Combine(dataDir, "settings.json");
        string backupPath = Path.Combine(dataDir, "settings.backup.json");

        var form = new Form
        {
            Text = "\u2692  " + T("settings_title"),
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(460, 240),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        var statusLabel = new Label
        {
            Text = "",
            Font = new Font("Segoe UI", 10),
            ForeColor = FG,
            AutoSize = false,
            Size = new Size(430, 45),
            Location = new Point(14, 16),
        };

        int bw = 130, bh = 40, gap = 10;
        int bx = 14;

        var btnBackup = new Button
        {
            Text = "\u2B07  " + T("settings_backup"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(bw, bh),
            Location = new Point(bx, 80),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = Color.FromArgb(100, 220, 130),
        };
        btnBackup.FlatAppearance.BorderColor = ACCENT_GREEN;
        bx += bw + gap;

        var btnRestore = new Button
        {
            Text = "\u2B06  " + T("settings_restore"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(bw, bh),
            Location = new Point(bx, 80),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = ACCENT_BLUE,
        };
        btnRestore.FlatAppearance.BorderColor = ACCENT_BLUE;
        bx += bw + gap;

        var btnDel = new Button
        {
            Text = "\u2716  " + T("settings_delete"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(bw, bh),
            Location = new Point(bx, 80),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = ACCENT_RED,
        };
        btnDel.FlatAppearance.BorderColor = ACCENT_RED;

        var btnClose = new Button
        {
            Text = T("exit"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(100, 34),
            Location = new Point(180, 150),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = FG2,
        };
        btnClose.FlatAppearance.BorderColor = FG3;

        Action refresh = () =>
        {
            bool hasSettings = File.Exists(settingsPath);
            bool hasBackup = File.Exists(backupPath);
            string info = "";

            if (hasSettings)
            {
                long sz = new FileInfo(settingsPath).Length;
                info += "\u2705 settings.json  (" + FormatSize(sz) + ")";
            }
            else
            {
                info += "\u274C settings.json  (" + T("settings_no_file") + ")";
            }

            if (hasBackup)
            {
                long sz = new FileInfo(backupPath).Length;
                info += "\n\u2705 settings.backup.json  (" + FormatSize(sz) + ")";
            }
            else
            {
                info += "\n\u25CB settings.backup.json  (" + T("settings_no_file") + ")";
            }

            statusLabel.Text = info;

            btnBackup.Enabled = hasSettings;
            btnBackup.ForeColor = hasSettings ? ACCENT_GREEN : FG3;
            btnBackup.FlatAppearance.BorderColor = hasSettings ? ACCENT_GREEN : FG3;

            btnRestore.Enabled = hasBackup;
            btnRestore.ForeColor = hasBackup ? ACCENT_BLUE : FG3;
            btnRestore.FlatAppearance.BorderColor = hasBackup ? ACCENT_BLUE : FG3;

            btnDel.Enabled = hasSettings;
            btnDel.ForeColor = hasSettings ? ACCENT_RED : FG3;
            btnDel.FlatAppearance.BorderColor = hasSettings ? ACCENT_RED : FG3;
        };
        refresh();

        // Backup- copy settings.json → settings.backup.json
        btnBackup.Click += (s, e) =>
        {
            try
            {
                File.Copy(settingsPath, backupPath, true);
                MessageBox.Show(T("settings_backup_ok") + "\n" + backupPath, "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Information);
                refresh();
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        };

        // Restore- copy settings.backup.json → settings.json
        btnRestore.Click += (s, e) =>
        {
            try
            {
                File.Copy(backupPath, settingsPath, true);
                MessageBox.Show(T("settings_restore_ok"), "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Information);
                refresh();
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        };

        // Delete
        btnDel.Click += (s, e) =>
        {
            if (MessageBox.Show(T("settings_delete_confirm"), "PuriPuly Heart GPU", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
            {
                try
                {
                    File.Delete(settingsPath);
                    MessageBox.Show(T("settings_delete_ok"), "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    refresh();
                }
                catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            }
        };

        btnClose.Click += (s, e) => form.Close();

        form.Controls.AddRange(new Control[] { statusLabel, btnBackup, btnRestore, btnDel, btnClose });
        form.ShowDialog();
    }

    // --- Performance dialog ---
    static DialogResult ShowPerformanceDialog(string dataDir)
    {
        var perf = LoadPerformanceSettings(dataDir);
        int pcoreCount = CountPCores();
        int totalCores = Environment.ProcessorCount;
        int ecoreCount = totalCores - pcoreCount;
        var numaNodes = DetectNumaNodes();
        bool isNuma = numaNodes.Length > 1;
        bool isMultiGroup = false;
        for (int i = 0; i < numaNodes.Length; i++)
            for (int j = i + 1; j < numaNodes.Length; j++)
                if (numaNodes[i].Group != numaNodes[j].Group) isMultiGroup = true;

        var form = new Form
        {
            Text = "\u26A1  " + T("perf_title"),
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(480, 470),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        // --- Tab: Priority ---
        var tabPriority = new TabPage(T("perf_tab_priority"));
        tabPriority.BackColor = BG;
        tabPriority.ForeColor = FG;

        var lblPriority = new Label
        {
            Text = T("perf_priority") + ":",
            Font = new Font("Segoe UI", 12),
            ForeColor = FG,
            AutoSize = true,
            Location = new Point(14, 14),
        };

        var cmbPriority = new ComboBox
        {
            Font = new Font("Segoe UI", 12),
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(14, 42),
            Size = new Size(420, 36),
            BackColor = BG3,
            ForeColor = FG,
        };
        cmbPriority.Items.AddRange(new object[] {
            T("priority_idle"),
            T("priority_below_normal"),
            T("priority_normal"),
            T("priority_above_normal"),
            T("priority_high"),
            T("priority_realtime"),
        });
        cmbPriority.SelectedIndex = perf.Priority;

        var lblPriorityDesc = new Label
        {
            Text = T("perf_priority_desc"),
            Font = new Font("Segoe UI", 10),
            ForeColor = FG3,
            AutoSize = false,
            Size = new Size(420, 70),
            Location = new Point(14, 88),
        };

        tabPriority.Controls.AddRange(new Control[] { lblPriority, cmbPriority, lblPriorityDesc });

        // --- Tab: Cores ---
        var tabCores = new TabPage(T("perf_tab_cores"));
        tabCores.BackColor = BG;
        tabCores.ForeColor = FG;

        var chkPCores = new CheckBox
        {
            Text = T("perf_pcores") + (isNuma ? "  (N/A \u2014 NUMA system)" : ""),
            Font = new Font("Segoe UI", 12),
            ForeColor = isNuma ? FG3 : ACCENT_BLUE,
            AutoSize = true,
            Location = new Point(18, 18),
            Checked = perf.PCoresOnly,
            Enabled = !isNuma,
        };

        var lblCoreInfo = new Label
        {
            Text = "P: " + pcoreCount + "   E: " + ecoreCount + "   Total: " + totalCores
                + (isNuma ? "   NUMA: " + numaNodes.Length + " nodes" : "")
                + (isMultiGroup ? "   [Multi-Group]" : ""),
            Font = new Font("Segoe UI", 11, FontStyle.Bold),
            ForeColor = ACCENT_GREEN,
            AutoSize = true,
            Location = new Point(18, 54),
        };

        var lblHint = new Label
        {
            Text = isNuma ? "\u2139  NUMA system detected. P/E core selection is not applicable.\nUse NUMA node selection to control core affinity." : "\u2139  " + T("perf_pcores_hint"),
            Font = new Font("Segoe UI", 10),
            ForeColor = FG3,
            AutoSize = false,
            Size = new Size(410, 55),
            Location = new Point(18, 88),
        };

        // --- NUMA controls (always visible, disabled if no NUMA) ---
        var numaCombo = new ComboBox
        {
            Font = new Font("Segoe UI", 9),
            BackColor = isNuma ? BG3 : BG2, ForeColor = isNuma ? FG : FG3,
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(18, 148),
            Size = new Size(200, 26),
            Enabled = isNuma,
        };
        numaCombo.Items.Add("NUMA: No restriction");
        numaCombo.Items.Add("NUMA: Single node");
        numaCombo.Items.Add("NUMA: First N nodes");
        numaCombo.SelectedIndex = perf.NumaMode;

        var numaNodeCombo = new ComboBox
        {
            Font = new Font("Segoe UI", 9),
            BackColor = isNuma ? BG3 : BG2, ForeColor = isNuma ? FG : FG3,
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(224, 148),
            Size = new Size(200, 26),
            Enabled = isNuma,
            Visible = isNuma && perf.NumaMode > 0,
        };
        // Populate NUMA node options based on mode
        Action<int> populateNumaNodes = (mode) =>
        {
            numaNodeCombo.Items.Clear();
            if (mode == 1)
            {
                for (int i = 0; i < numaNodes.Length; i++)
                    numaNodeCombo.Items.Add("Node " + numaNodes[i].NodeNumber + " (" + numaNodes[i].CoreCount + " cores)");
                if (perf.NumaNode < numaNodes.Length) numaNodeCombo.SelectedIndex = perf.NumaNode;
                else if (numaNodes.Length > 0) numaNodeCombo.SelectedIndex = 0;
            }
            else if (mode == 2)
            {
                for (int i = 1; i <= numaNodes.Length; i++)
                    numaNodeCombo.Items.Add("First " + i + " node" + (i > 1 ? "s" : ""));
                int sel = Math.Max(1, Math.Min(perf.NumaNode, numaNodes.Length));
                numaNodeCombo.SelectedIndex = sel - 1;
            }
        };
        populateNumaNodes(perf.NumaMode);

        numaCombo.SelectedIndexChanged += (s, e) =>
        {
            int mode = numaCombo.SelectedIndex;
            numaNodeCombo.Visible = mode > 0;
            populateNumaNodes(mode);
        };

        Label lblNumaWarn = null;
        if (isMultiGroup)
        {
            lblNumaWarn = new Label
            {
                Text = "\u26A0 Multi-group NUMA detected. P-core affinity limited to one CPU group.",
                Font = new Font("Segoe UI", 9),
                ForeColor = ACCENT_YELLOW,
                AutoSize = false,
                Size = new Size(410, 20),
                Location = new Point(18, 180),
            };
        }

        var numaControls = new System.Collections.Generic.List<Control> { chkPCores, lblCoreInfo, lblHint, numaCombo, numaNodeCombo };
        if (lblNumaWarn != null) numaControls.Add(lblNumaWarn);
        tabCores.Controls.AddRange(numaControls.ToArray());

        // --- Tab: GPU ---
        var tabGpu = new TabPage(T("perf_tab_gpu"));
        tabGpu.BackColor = BG;
        tabGpu.ForeColor = FG;

        var gpuList = DetectGpus();
        var vulkanDevices = DetectVulkanDevices();
        var vulkanGpus = vulkanDevices.FindAll(d => d.Kind == "gpu");

        // -- Section: DirectML (ONNX) --
        var lblDirectML = new Label
        {
            Text = "DirectML",
            Font = new Font("Segoe UI", 11, FontStyle.Bold),
            ForeColor = FG,
            AutoSize = true,
            Location = new Point(14, 10),
        };

        var lblGpuDevice = new Label
        {
            Text = T("perf_gpu_device") + ":",
            Font = new Font("Segoe UI", 11),
            ForeColor = FG,
            AutoSize = true,
            Location = new Point(14, 34),
        };

        var cmbGpu = new ComboBox
        {
            Font = new Font("Segoe UI", 11),
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(14, 58),
            Size = new Size(420, 36),
            BackColor = BG3,
            ForeColor = FG,
        };
        for (int i = 0; i < gpuList.Count; i++)
        {
            string luid = LuidToString(gpuList[i].AdapterLuid);
            cmbGpu.Items.Add("GPU " + i + ": " + gpuList[i].Name + "  [LUID " + luid + "]");
        }
        int gpuSel = perf.GpuDevice;
        if (!string.IsNullOrEmpty(perf.GpuLuid))
        {
            int luidIdx = FindGpuIndexByLuid(gpuList, perf.GpuLuid);
            if (luidIdx >= 0) gpuSel = luidIdx;
        }
        cmbGpu.SelectedIndex = Math.Min(gpuSel, cmbGpu.Items.Count - 1);

        // -- Separator --
        var sep1 = new Label
        {
            BorderStyle = BorderStyle.Fixed3D,
            AutoSize = false,
            Height = 2,
            Width = 420,
            Location = new Point(14, 104),
        };

        // -- Section: Vulkan (GGUF) --
        var lblVulkan = new Label
        {
            Text = "Vulkan",
            Font = new Font("Segoe UI", 11, FontStyle.Bold),
            ForeColor = FG,
            AutoSize = true,
            Location = new Point(14, 114),
        };

        var lblVulkanDevice = new Label
        {
            Text = T("perf_vulkan_device") + ":",
            Font = new Font("Segoe UI", 11),
            ForeColor = FG,
            AutoSize = true,
            Location = new Point(14, 138),
        };

        var cmbVulkan = new ComboBox
        {
            Font = new Font("Segoe UI", 11),
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(14, 162),
            Size = new Size(420, 36),
            BackColor = BG3,
            ForeColor = FG,
        };
        cmbVulkan.Items.Add(T("perf_gpu_auto"));
        for (int i = 0; i < vulkanGpus.Count; i++)
        {
            string vluid = vulkanGpus[i].Luid != null ? "  [LUID " + vulkanGpus[i].Luid + "]" : "";
            cmbVulkan.Items.Add("GPU " + vulkanGpus[i].Index + ": " + vulkanGpus[i].Name + vluid);
        }
        int vulkanSel = 0;
        if (!string.IsNullOrEmpty(perf.GpuLuid))
        {
            for (int i = 0; i < vulkanGpus.Count; i++)
                if (vulkanGpus[i].Luid == perf.GpuLuid) { vulkanSel = i + 1; break; }
        }
        else if (perf.VulkanDevice > 0)
        {
            for (int i = 0; i < vulkanGpus.Count; i++)
                if (vulkanGpus[i].Index == perf.VulkanDevice) { vulkanSel = i + 1; break; }
        }
        cmbVulkan.SelectedIndex = Math.Min(vulkanSel, cmbVulkan.Items.Count - 1);

        var lblGpuHintAll = new Label
        {
            Text = "\u2139  " + T("perf_gpu_hint") + "\n" + T("perf_vulkan_hint"),
            Font = new Font("Segoe UI", 10),
            ForeColor = FG3,
            AutoSize = false,
            Size = new Size(420, 55),
            Location = new Point(14, 208),
        };

        tabGpu.Controls.AddRange(new Control[] {
            lblDirectML, lblGpuDevice, cmbGpu, sep1,
            lblVulkan, lblVulkanDevice, cmbVulkan, lblGpuHintAll
        });

        // --- TabControl ---
        var tabs = new TabControl
        {
            Location = new Point(8, 8),
            Size = new Size(456, 290),
        };
        tabs.TabPages.Add(tabPriority);
        tabs.TabPages.Add(tabCores);
        tabs.TabPages.Add(tabGpu);

        // --- Bottom buttons ---
        var statusLabel = new Label
        {
            Text = "",
            Font = new Font("Segoe UI", 10),
            ForeColor = ACCENT_GREEN,
            AutoSize = false,
            Size = new Size(300, 28),
            Location = new Point(14, 310),
        };

        var btnSave = new Button
        {
            Text = "\u2714  " + T("perf_save"),
            Font = new Font("Segoe UI", 11),
            Size = new Size(140, 40),
            Location = new Point(14, 345),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_GREEN,
        };
        btnSave.FlatAppearance.BorderColor = ACCENT_GREEN;

        var btnClose = new Button
        {
            Text = T("exit"),
            Font = new Font("Segoe UI", 11),
            Size = new Size(120, 40),
            Location = new Point(164, 345),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = FG2,
        };
        btnClose.FlatAppearance.BorderColor = FG3;

        btnSave.Click += (s, e) =>
        {
            string oldLuid = perf.GpuLuid;
            perf.Priority = cmbPriority.SelectedIndex;
            perf.PCoresOnly = chkPCores.Checked;
            perf.GpuDevice = cmbGpu.SelectedIndex;
            // Save LUID for the selected DXGI GPU
            perf.GpuLuid = cmbGpu.SelectedIndex >= 0 && cmbGpu.SelectedIndex < gpuList.Count
                ? LuidToString(gpuList[cmbGpu.SelectedIndex].AdapterLuid) : null;
            // Legacy Vulkan index (still saved for backward compat)
            perf.VulkanDevice = cmbVulkan.SelectedIndex > 0 && cmbVulkan.SelectedIndex - 1 < vulkanGpus.Count
                ? vulkanGpus[cmbVulkan.SelectedIndex - 1].Index : 0;
            perf.NumaMode = numaCombo.SelectedIndex;
            perf.NumaNode = numaNodeCombo.Visible ? numaNodeCombo.SelectedIndex + (numaCombo.SelectedIndex == 2 ? 1 : 0) : 0;
            SavePerformanceSettings(dataDir, perf);
            SetGpuPreferenceRegistry(perf.GpuDevice, gpuList, dataDir);
            statusLabel.Text = "\u2705 " + T("perf_saved");
            if (perf.GpuLuid != oldLuid)
            {
                MessageBox.Show(T("perf_gpu_restart_notice"), "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Information);
                form.DialogResult = DialogResult.Retry;
                form.Close();
            }
        };

        btnClose.Click += (s, e) => form.Close();

        form.Controls.AddRange(new Control[] {
            tabs, statusLabel, btnSave, btnClose
        });
        return form.ShowDialog();
    }

    // --- Models management dialog ---
    static void ShowModelsDialog()
    {
        int maxH = (int)(Screen.PrimaryScreen.WorkingArea.Height * 0.85);
        int maxW = (int)(Screen.PrimaryScreen.WorkingArea.Width * 0.9);
        int idealH = 40 + 25 + 20 + MODELS.Length * 22 + 80 + 40;
        var form = new Form
        {
            Text = "\u2699  " + T("models_title"),
            FormBorderStyle = FormBorderStyle.Sizable,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(560, Math.Min(idealH, maxH)),
            MinimumSize = new Size(480, 300),
            BackColor = BG,
            MaximizeBox = true,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        // --- Search box ---
        var searchBox = new TextBox
        {
            Font = new Font("Segoe UI", 10),
            BackColor = BG3, ForeColor = FG,
            BorderStyle = BorderStyle.FixedSingle,
            Location = new Point(8, 8), Size = new Size(form.ClientSize.Width - 320, 26),
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right,
        };

        // --- Language filter ---
        var langCombo = new ComboBox
        {
            Font = new Font("Segoe UI", 9),
            BackColor = BG3, ForeColor = FG,
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(form.ClientSize.Width - 304, 8),
            Size = new Size(150, 26),
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
        };
        langCombo.Items.Add("\U0001F30D " + "All languages");
        for (int li = 0; li < LANG_NAMES.Length; li++)
            langCombo.Items.Add(LANG_NAMES[li][1] + "  (" + LANG_NAMES[li][0] + ")");
        langCombo.SelectedIndex = 0;

        // --- Quant filter ---
        var quantCombo = new ComboBox
        {
            Font = new Font("Segoe UI", 9),
            BackColor = BG3, ForeColor = FG,
            DropDownStyle = ComboBoxStyle.DropDownList,
            Location = new Point(form.ClientSize.Width - 148, 8),
            Size = new Size(140, 26),
            Anchor = AnchorStyles.Top | AnchorStyles.Right,
        };
        quantCombo.Items.Add("All quant");
        quantCombo.Items.Add("INT8");
        quantCombo.Items.Add("BF16");
        quantCombo.Items.Add("FP16");
        quantCombo.Items.Add("FP32");
        quantCombo.Items.Add("Q4_K");
        quantCombo.Items.Add("Q5_K");
        quantCombo.Items.Add("Q6_K");
        quantCombo.Items.Add("Q8");
        quantCombo.SelectedIndex = 0;

        // --- TabControl ---
        var tabs = new TabControl
        {
            Location = new Point(8, 40),
            Size = new Size(form.ClientSize.Width - 16, form.ClientSize.Height - 120),
            Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right,
        };

        // Build category tabs + "All"
        var listViews = new Dictionary<string, ListView>();
        for (int ci = 0; ci < MODEL_CATEGORIES.Length; ci++)
        {
            string catKey = MODEL_CATEGORIES[ci][0];
            string catLabel = MODEL_CATEGORIES[ci][1];
            var tabPage = new TabPage(catLabel) { BackColor = BG, Tag = catKey };
            var lv = CreateModelListView();
            tabPage.Controls.Add(lv);
            tabs.TabPages.Add(tabPage);
            listViews[catKey] = lv;
        }
        var allTab = new TabPage("\U0001F4E6  All") { BackColor = BG, Tag = "__all__" };
        var allLv = CreateModelListView();
        allTab.Controls.Add(allLv);
        tabs.TabPages.Add(allTab);
        listViews["__all__"] = allLv;

        // --- Bottom panel: buttons + links ---
        var bottomPanel = new Panel
        {
            Dock = DockStyle.Bottom, Height = 72, BackColor = BG,
        };

        int bw2 = 100, bh2 = 28, gap2 = 8;
        var btnDl = new Button { Text = "\u2B07 " + T("download"), Font = new Font("Segoe UI", 9), Size = new Size(bw2, bh2), Location = new Point(8, 4), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = ACCENT_GREEN, Enabled = false };
        btnDl.FlatAppearance.BorderColor = ACCENT_GREEN;
        var btnVerify = new Button { Text = "\u2714 " + T("verify"), Font = new Font("Segoe UI", 9), Size = new Size(bw2, bh2), Location = new Point(8 + bw2 + gap2, 4), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = ACCENT_BLUE, Enabled = false };
        btnVerify.FlatAppearance.BorderColor = ACCENT_BLUE;
        var btnDel = new Button { Text = "\u2716 " + T("delete"), Font = new Font("Segoe UI", 9), Size = new Size(bw2, bh2), Location = new Point(8 + (bw2 + gap2) * 2, 4), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = ACCENT_RED, Enabled = false };
        btnDel.FlatAppearance.BorderColor = ACCENT_RED;
        var statusLabel = new Label { Text = "", Font = new Font("Segoe UI", 8.5f), ForeColor = FG2, AutoSize = false, Size = new Size(400, 18), Location = new Point(8, 36), Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right };
        var linksPanel = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, AutoSize = true, Location = new Point(8, 54), WrapContents = false, Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right };
        var hfLink = new LinkLabel { Text = "", Font = new Font("Segoe UI", 8), AutoSize = true, LinkColor = ACCENT_YELLOW, Visible = false };
        var msSep = new Label { Text = "  |  ", Font = new Font("Segoe UI", 8), ForeColor = FG3, AutoSize = true, Visible = false };
        var msLink = new LinkLabel { Text = "ModelScope", Font = new Font("Segoe UI", 8), AutoSize = true, LinkColor = Color.FromArgb(100, 200, 255), Visible = false };
        linksPanel.Controls.AddRange(new Control[] { hfLink, msSep, msLink });
        bottomPanel.Controls.AddRange(new Control[] { btnDl, btnVerify, btnDel, statusLabel, linksPanel });

        form.Controls.AddRange(new Control[] { searchBox, langCombo, quantCombo, tabs, bottomPanel });

        // --- State ---
        int selectedIdx = -1;
        Action<int> updateButtons = (idx) =>
        {
            selectedIdx = idx;
            if (idx < 0)
            {
                btnDl.Enabled = btnVerify.Enabled = btnDel.Enabled = false;
                btnDl.ForeColor = btnVerify.ForeColor = btnDel.ForeColor = FG3;
                btnDl.FlatAppearance.BorderColor = btnVerify.FlatAppearance.BorderColor = btnDel.FlatAppearance.BorderColor = FG3;
                statusLabel.Text = "";
                hfLink.Visible = msSep.Visible = msLink.Visible = false;
                return;
            }
            int found; long fsize;
            var st = CheckModel(idx, out found, out fsize);
            string stText; Color stColor;
            switch (st)
            {
                case ModelStatus.Ready:
                    stText = "\u2705 " + T("installed") + " (" + found + "/" + FILES[idx].Length + ")  \u2014  " + FormatSize(fsize) + " " + T("on_disk");
                    stColor = ACCENT_GREEN;
                    btnDl.Enabled = false; btnDl.ForeColor = FG3; btnDl.FlatAppearance.BorderColor = FG3;
                    btnVerify.Enabled = true; btnVerify.ForeColor = ACCENT_BLUE; btnVerify.FlatAppearance.BorderColor = ACCENT_BLUE;
                    btnDel.Enabled = true; btnDel.ForeColor = ACCENT_RED; btnDel.FlatAppearance.BorderColor = ACCENT_RED;
                    break;
                case ModelStatus.Incomplete:
                    stText = "\u26A0\uFE0F " + T("incomplete") + " (" + found + "/" + FILES[idx].Length + ")  \u2014  " + FormatSize(fsize) + " / ~" + FormatSize(ModelTotalSize(idx));
                    stColor = ACCENT_YELLOW;
                    btnDl.Enabled = true; btnDl.ForeColor = ACCENT_GREEN; btnDl.FlatAppearance.BorderColor = ACCENT_GREEN;
                    btnVerify.Enabled = true; btnVerify.ForeColor = ACCENT_BLUE; btnVerify.FlatAppearance.BorderColor = ACCENT_BLUE;
                    btnDel.Enabled = true; btnDel.ForeColor = ACCENT_RED; btnDel.FlatAppearance.BorderColor = ACCENT_RED;
                    break;
                default:
                    stText = "\u274C " + T("not_installed") + "  \u2014  ~" + FormatSize(ModelTotalSize(idx));
                    stColor = ACCENT_RED;
                    btnDl.Enabled = true; btnDl.ForeColor = ACCENT_GREEN; btnDl.FlatAppearance.BorderColor = ACCENT_GREEN;
                    btnVerify.Enabled = false; btnVerify.ForeColor = FG3; btnVerify.FlatAppearance.BorderColor = FG3;
                    btnDel.Enabled = false; btnDel.ForeColor = FG3; btnDel.FlatAppearance.BorderColor = FG3;
                    break;
            }
            statusLabel.Text = MODELS[idx][1] + "  |  " + stText;
            statusLabel.ForeColor = stColor;
            string repoUrl = MODELS[idx][2].Substring(0, MODELS[idx][2].IndexOf("/resolve/"));
            hfLink.Text = "HuggingFace";
            hfLink.Tag = repoUrl;
            hfLink.Visible = true;
            if (MODELS_MODELSCOPE_REPO[idx] != null)
            {
                msSep.Visible = true;
                msLink.Visible = true;
                msLink.Tag = MODELS_MODELSCOPE_REPO[idx];
            }
            else
            {
                msSep.Visible = false;
                msLink.Visible = false;
            }
        };

        // --- Populate ListView ---
        Action<string> populate = (filter) =>
        {
            filter = (filter ?? "").Trim().ToLowerInvariant();
            string selLang = langCombo.SelectedIndex > 0 ? LANG_NAMES[langCombo.SelectedIndex - 1][0] : null;
            string selQuant = quantCombo.SelectedIndex > 0 ? quantCombo.Items[quantCombo.SelectedIndex].ToString() : null;
            foreach (var kv in listViews)
            {
                kv.Value.Items.Clear();
                for (int mi = 0; mi < MODELS.Length; mi++)
                {
                    string cat = MODELS[mi].Length > 3 ? MODELS[mi][3] : "onnx";
                    if (kv.Key != "__all__" && cat != kv.Key) continue;
                    if (filter.Length > 0 && MODELS[mi][1].ToLowerInvariant().IndexOf(filter) < 0 && MODELS[mi][0].ToLowerInvariant().IndexOf(filter) < 0) continue;
                    if (selLang != null && mi < MODELS_LANGS.Length)
                    {
                        bool found2 = false;
                        for (int lj = 0; lj < MODELS_LANGS[mi].Length; lj++)
                            if (MODELS_LANGS[mi][lj] == selLang) { found2 = true; break; }
                        if (!found2) continue;
                    }
                    if (selQuant != null && DetectModelQuant(mi) != selQuant) continue;

                    int found; long fsize;
                    var st = CheckModel(mi, out found, out fsize);
                    string stIcon = st == ModelStatus.Ready ? "\u2705" : st == ModelStatus.Incomplete ? "\u26A0\uFE0F" : "\u274C";
                    string stText = st == ModelStatus.Ready ? T("installed") : st == ModelStatus.Incomplete ? T("incomplete") : T("not_installed");
                    string format = DetectModelFormat(mi);
                    string quant = DetectModelQuant(mi);

                    string langs = mi < MODELS_LANGS.Length ? string.Join(", ", MODELS_LANGS[mi]) : "\u2014";

                    var item = new ListViewItem(new[] {
                        MODELS[mi][1],
                        format,
                        quant,
                        "~" + FormatSize(ModelTotalSize(mi)),
                        langs,
                        stIcon + " " + stText
                    });
                    item.Tag = mi;
                    if (st == ModelStatus.Ready) item.ForeColor = ACCENT_GREEN;
                    else if (st == ModelStatus.Incomplete) item.ForeColor = ACCENT_YELLOW;
                    kv.Value.Items.Add(item);
                }
                for (int c = 0; c < kv.Value.Columns.Count; c++)
                    kv.Value.AutoResizeColumn(c, ColumnHeaderAutoResizeStyle.ColumnContent);
            }
            // Auto-size form width to fit widest ListView
            int totalColW = 0;
            foreach (var lv2 in listViews.Values)
            {
                int w = 0;
                for (int c = 0; c < lv2.Columns.Count; c++) w += lv2.Columns[c].Width;
                if (w > totalColW) totalColW = w;
            }
            int desiredW = totalColW + 40;
            if (desiredW > form.ClientSize.Width && desiredW <= maxW)
                form.Width = desiredW + (form.Width - form.ClientSize.Width);
        };

        // --- Wire events ---
        ListView activeLv = null;
        Action syncActiveLv = () =>
        {
            var tp = tabs.SelectedTab;
            activeLv = tp != null ? (ListView)tp.Controls[0] : allLv;
        };

        populate(null);
        tabs.SelectedIndexChanged += (s, e) => { syncActiveLv(); updateButtons(-1); };
        syncActiveLv();

        foreach (var kv in listViews)
        {
            var lv = kv.Value;
            lv.SelectedIndexChanged += (s, e) =>
            {
                if (lv.SelectedItems.Count > 0)
                    updateButtons((int)lv.SelectedItems[0].Tag);
                else
                    updateButtons(-1);
            };
            lv.DoubleClick += (s, e) =>
            {
                if (lv.SelectedItems.Count > 0)
                {
                    int idx = (int)lv.SelectedItems[0].Tag;
                    ShowDownloader(idx, null, () => { populate(searchBox.Text); updateButtons(idx); });
                }
            };
        }

        searchBox.TextChanged += (s, e) => { populate(searchBox.Text); updateButtons(-1); };
        langCombo.SelectedIndexChanged += (s, e) => { populate(searchBox.Text); updateButtons(-1); };
        quantCombo.SelectedIndexChanged += (s, e) => { populate(searchBox.Text); updateButtons(-1); };

        hfLink.Click += (s, e) => { if (hfLink.Tag != null) try { Process.Start(hfLink.Tag.ToString()); } catch { } };
        msLink.Click += (s, e) => { if (msLink.Tag != null) try { Process.Start(msLink.Tag.ToString()); } catch { } };

        btnDl.Click += (s, e) => { if (selectedIdx >= 0) { int si = selectedIdx; ShowDownloader(si, null, () => { populate(searchBox.Text); updateButtons(si); }); } };
        btnVerify.Click += (s, e) =>
        {
            if (selectedIdx < 0) return;
            int idx = selectedIdx;
            string dir = Path.Combine(ModelsDir, ModelInstallDir(idx));
            int ok = 0, bad = 0;
            List<int> badIndices = new List<int>();
            string details = "";

            var vs = new Form { Text = MODELS[idx][1], FormBorderStyle = FormBorderStyle.FixedSingle, StartPosition = FormStartPosition.CenterParent, Size = new Size(420, 120), BackColor = BG, TopMost = true, ShowInTaskbar = false, ControlBox = false };
            var vl = new Label { Text = "", Font = new Font("Segoe UI", 10), ForeColor = FG, AutoSize = false, Size = new Size(390, 22), Location = new Point(12, 12) };
            var vb = new ProgressBar { Location = new Point(12, 42), Size = new Size(390, 22), Style = ProgressBarStyle.Continuous, Maximum = FILES[idx].Length };
            vs.Controls.Add(vl);
            vs.Controls.Add(vb);
            vs.Show();
            vs.Refresh();

            for (int fi = 0; fi < FILES[idx].Length; fi++)
            {
                vl.Text = LOCAL_FILES[idx][fi];
                vb.Value = fi + 1;
                vl.Refresh();
                vb.Refresh();
                Application.DoEvents();
                string fp = Path.Combine(dir, LOCAL_FILES[idx][fi]);
                if (File.Exists(fp))
                {
                    long sz = new FileInfo(fp).Length;
                    if (sz == SIZES[idx][fi])
                    {
                        uint crc = ComputeCRC32(fp);
                        if (crc == CRC32S[idx][fi]) ok++;
                        else { bad++; badIndices.Add(fi); details += "\n  " + LOCAL_FILES[idx][fi] + " \u2014 " + T("file_damaged"); }
                    }
                    else { bad++; badIndices.Add(fi); details += "\n  " + LOCAL_FILES[idx][fi] + " \u2014 " + T("file_incomplete"); }
                }
                else { bad++; badIndices.Add(fi); details += "\n  " + LOCAL_FILES[idx][fi] + " \u2014 " + T("file_not_found"); }
            }
            vs.Close();
            vs.Dispose();
            if (bad == 0)
            {
                EnsureInstalledManifest(idx);
                MessageBox.Show(T("all_ok") + "\n" + ok + "/" + FILES[idx].Length, MODELS[idx][1], MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            else
            {
                var res = MessageBox.Show(
                    ok + " " + T("files_ok") + ", " + bad + " " + T("files_with_problems") + ":\n" + details + "\n\n" + T("redownload_question"),
                    MODELS[idx][1], MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                if (res == DialogResult.Yes) ShowDownloader(idx, badIndices.ToArray(), () => { populate(searchBox.Text); updateButtons(idx); });
            }
        };
        btnDel.Click += (s, e) =>
        {
            if (selectedIdx < 0) return;
            int idx = selectedIdx;
            if (MessageBox.Show(T("confirm_delete") + "\n" + MODELS[idx][1], T("confirm_delete"), MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
            {
                try { Directory.Delete(Path.Combine(ModelsDir, ModelInstallDir(idx)), true); Directory.CreateDirectory(Path.Combine(ModelsDir, ModelInstallDir(idx))); }
                catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
                populate(searchBox.Text);
                updateButtons(idx);
            }
        };

        form.ShowDialog();
    }

    static string DetectModelFormat(int mi)
    {
        for (int fi = 0; fi < FILES[mi].Length; fi++)
        {
            string f = FILES[mi][fi].ToLowerInvariant();
            if (f.EndsWith(".gguf")) return "GGUF";
        }
        for (int fi = 0; fi < FILES[mi].Length; fi++)
        {
            string f = FILES[mi][fi].ToLowerInvariant();
            if (f.EndsWith(".onnx")) return "ONNX";
        }
        return "\u2014";
    }

    static string DetectModelQuant(int mi)
    {
        bool hasInt8 = false, hasFp16 = false, hasFp32 = false, hasBf16 = false;
        bool hasQ4 = false, hasQ5 = false, hasQ6 = false, hasQ8 = false;
        for (int fi = 0; fi < FILES[mi].Length; fi++)
        {
            string f = FILES[mi][fi].ToLowerInvariant();
            if (f.Contains(".int8.") || f.Contains("_int8.")) hasInt8 = true;
            if (f.Contains(".fp16.") || (f.EndsWith(".gguf") && f.Contains("-f16."))) hasFp16 = true;
            if (f.Contains(".fp32.") || (!hasInt8 && !hasFp16 && f.EndsWith(".onnx"))) hasFp32 = true;
            if (f.EndsWith(".gguf") && f.Contains("-bf16.")) hasBf16 = true;
            if (f.Contains("q4_k")) hasQ4 = true;
            if (f.Contains("q5_k")) hasQ5 = true;
            if (f.Contains("q6_k")) hasQ6 = true;
            if (f.Contains("q8_0") || f.Contains("q8_k")) hasQ8 = true;
        }
        if (hasInt8) return "INT8";
        if (hasBf16) return "BF16";
        if (hasFp16) return "FP16";
        if (hasQ4) return "Q4_K";
        if (hasQ5) return "Q5_K";
        if (hasQ6) return "Q6_K";
        if (hasQ8) return "Q8";
        if (hasFp32) return "FP32";
        return "\u2014";
    }

    static int _lvSortColumn = -1;
    static bool _lvSortAscending = true;

    static ListView CreateModelListView()
    {
        var lv = new ListView
        {
            View = View.Details,
            FullRowSelect = true,
            GridLines = false,
            HeaderStyle = ColumnHeaderStyle.Clickable,
            Font = new Font("Segoe UI", 9),
            BackColor = BG2, ForeColor = FG,
            BorderStyle = BorderStyle.None,
            Dock = DockStyle.Fill,
            MultiSelect = false,
        };
        lv.Columns.Add("Name", 120);
        lv.Columns.Add("Format", 40);
        lv.Columns.Add("Quant", 40);
        lv.Columns.Add("Size", 50);
        lv.Columns.Add("Languages", 80);
        lv.Columns.Add("Status", 60);
        lv.ListViewItemSorter = new ListViewColumnSorter();
        lv.ColumnClick += (s, e) =>
        {
            if (_lvSortColumn == e.Column)
            {
                _lvSortAscending = !_lvSortAscending;
            }
            else
            {
                _lvSortColumn = e.Column;
                _lvSortAscending = true;
            }
            var sorter = (ListViewColumnSorter)lv.ListViewItemSorter;
            sorter.Column = _lvSortColumn;
            sorter.Ascending = _lvSortAscending;
            lv.Sort();
        };
        return lv;
    }

    class ListViewColumnSorter : System.Collections.IComparer
    {
        public int Column = 0;
        public bool Ascending = true;

        public int Compare(object x, object y)
        {
            var a = (ListViewItem)x;
            var b = (ListViewItem)y;
            string va = Column < a.SubItems.Count ? a.SubItems[Column].Text : "";
            string vb = Column < b.SubItems.Count ? b.SubItems[Column].Text : "";

            if (Column == 3)
            {
                long sa = ParseSizeBytes(va);
                long sb = ParseSizeBytes(vb);
                int cmp = sa.CompareTo(sb);
                return Ascending ? cmp : -cmp;
            }

            if (Column == 5)
            {
                int sa = va.Contains("\u2705") ? 2 : va.Contains("\u26A0") ? 1 : 0;
                int sb = vb.Contains("\u2705") ? 2 : vb.Contains("\u26A0") ? 1 : 0;
                int cmp = sa.CompareTo(sb);
                return Ascending ? cmp : -cmp;
            }

            int cmp2 = string.Compare(va, vb, StringComparison.OrdinalIgnoreCase);
            return Ascending ? cmp2 : -cmp2;
        }
    }

    static long ParseSizeBytes(string s)
    {
        s = s.Trim();
        if (s.StartsWith("~")) s = s.Substring(1);
        s = s.Trim();
        double num;
        if (s.EndsWith("GB", StringComparison.OrdinalIgnoreCase))
        {
            if (double.TryParse(s.Substring(0, s.Length - 2).Trim(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out num))
                return (long)(num * 1024 * 1024 * 1024);
        }
        else if (s.EndsWith("MB", StringComparison.OrdinalIgnoreCase))
        {
            if (double.TryParse(s.Substring(0, s.Length - 2).Trim(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out num))
                return (long)(num * 1024 * 1024);
        }
        else if (s.EndsWith("KB", StringComparison.OrdinalIgnoreCase))
        {
            if (double.TryParse(s.Substring(0, s.Length - 2).Trim(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out num))
                return (long)(num * 1024);
        }
        long bytes;
        if (long.TryParse(s, out bytes)) return bytes;
        return 0;
    }

    // --- Helpers ---
    static string GetMirrorUrl(string hfUrl)
    {
        if (hfUrl.StartsWith("https://huggingface.co/"))
            return HF_MIRROR_BASE + hfUrl.Substring("https://huggingface.co/".Length);
        return hfUrl;
    }

    static void SetSourceBtnStyle(Button btn, bool active, Color fgColor, Color activeColor)
    {
        btn.BackColor = active ? Color.FromArgb(40, 45, 60) : Color.FromArgb(50, 55, 70);
        btn.ForeColor = active ? activeColor : fgColor;
        btn.FlatAppearance.BorderColor = active ? activeColor : Color.FromArgb(80, 85, 100);
        btn.Font = new Font("Segoe UI", 9f, active ? FontStyle.Bold : FontStyle.Regular);
    }

    // --- Downloader ---
    static HashSet<int> _activeDownloads = new HashSet<int>();
    static void ShowDownloader(int mi, int[] onlyFiles, Action onComplete)
    {
        if (_activeDownloads.Contains(mi))
        {
            MessageBox.Show(T("downloading") + " — " + MODELS[mi][1], T("downloading"), MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        _activeDownloads.Add(mi);
        string targetDir = Path.Combine(ModelsDir, ModelInstallDir(mi));
        string modelDisplay = MODELS[mi][1];
        bool cancelled = false;
        Exception downloadError = null;

        int currentSource = 0; // 0=HF, 1=Mirror, 2=MS

        int[] dlFiles = onlyFiles ?? new int[FILES[mi].Length];
        if (onlyFiles == null) { for (int i = 0; i < FILES[mi].Length; i++) dlFiles[i] = i; }

        long totalBytes = 0;
        foreach (int fi in dlFiles) totalBytes += SIZES[mi][fi];

        bool hasMs = MODELS_MODELSCOPE[mi] != null;
        int formWidth = 520;

        var form = new Form
        {
            Text = modelDisplay,
            FormBorderStyle = FormBorderStyle.FixedSingle,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(formWidth, 200),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        // --- Phase 1 controls: source selection ---
        var sourcePanel = new Panel { Location = new Point(12, 10), Size = new Size(formWidth - 40, 32), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
        var btnSrcHF = new Button { Text = T("src_hf"), Size = new Size(110, 28), Location = new Point(0, 2), FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand };
        var btnSrcMirror = new Button { Text = T("src_mirror"), Size = new Size(110, 28), Location = new Point(114, 2), FlatStyle = FlatStyle.Flat, Cursor = Cursors.Hand };
        var btnSrcMS = new Button { Text = T("src_ms"), Size = new Size(110, 28), Location = new Point(228, 2), FlatStyle = FlatStyle.Flat, Cursor = hasMs ? Cursors.Hand : Cursors.Default, Enabled = hasMs };
        sourcePanel.Controls.AddRange(new Control[] { btnSrcHF, btnSrcMirror, btnSrcMS });

        var sizeLabel = new Label { Text = T("size") + ": ~" + FormatSize(totalBytes), Font = new Font("Segoe UI", 9), ForeColor = FG2, AutoSize = false, Size = new Size(formWidth - 40, 20), Location = new Point(12, 48), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
        var sourceLabel = new Label { Text = "huggingface.co", Font = new Font("Segoe UI", 8), ForeColor = FG3, AutoSize = false, Size = new Size(formWidth - 40, 16), Location = new Point(12, 68), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
        var btnStart = new Button { Text = T("download"), Font = new Font("Segoe UI", 10), Size = new Size(140, 32), Location = new Point((formWidth - 140) / 2, 90), FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(30, 120, 60), ForeColor = Color.White, Cursor = Cursors.Hand, Anchor = AnchorStyles.Top };
        btnStart.FlatAppearance.BorderColor = Color.FromArgb(60, 160, 90);

        // --- Phase 2 controls: progress (hidden initially) ---
        var statusLabel = new Label { Text = "", Font = new Font("Segoe UI", 10), ForeColor = FG, AutoSize = false, Size = new Size(formWidth - 40, 22), Location = new Point(12, 10), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right, Visible = false };
        var fileLabel = new Label { Text = "", Font = new Font("Segoe UI", 8), ForeColor = FG2, AutoSize = false, Size = new Size(formWidth - 40, 18), Location = new Point(12, 36), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right, Visible = false };
        var progressBar = new ProgressBar { Minimum = 0, Maximum = 1000, Value = 0, Size = new Size(formWidth - 40, 24), Location = new Point(12, 60), Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right, Visible = false };
        var speedLabel = new Label { Text = "", Font = new Font("Segoe UI", 9), ForeColor = FG2, AutoSize = false, Size = new Size(200, 20), Location = new Point(12, 90), Anchor = AnchorStyles.Top | AnchorStyles.Left, Visible = false };
        var etaLabel = new Label { Text = "", Font = new Font("Segoe UI", 9), ForeColor = ACCENT_PINK, AutoSize = false, Size = new Size(formWidth - 240, 20), Location = new Point(222, 90), TextAlign = ContentAlignment.TopRight, Anchor = AnchorStyles.Top | AnchorStyles.Right, Visible = false };
        var btnCancel = new Button { Text = T("cancel"), Font = new Font("Segoe UI", 9), Size = new Size(90, 28), Location = new Point((formWidth - 90) / 2, 116), FlatStyle = FlatStyle.Flat, BackColor = BG3, ForeColor = FG, Anchor = AnchorStyles.Top, Visible = false };
        btnCancel.FlatAppearance.BorderColor = FG3;

        form.Controls.AddRange(new Control[] {
            sourcePanel, sizeLabel, sourceLabel, btnStart,
            statusLabel, fileLabel, progressBar, speedLabel, etaLabel, btnCancel
        });
        form.ControlBox = false;

        Action<int> selectSource = (src) =>
        {
            currentSource = src;
            SetSourceBtnStyle(btnSrcHF, src == 0, FG, Color.FromArgb(100, 200, 255));
            SetSourceBtnStyle(btnSrcMirror, src == 1, FG, Color.FromArgb(100, 200, 255));
            SetSourceBtnStyle(btnSrcMS, src == 2, FG, Color.FromArgb(200, 130, 20));
            sourceLabel.Text = src == 1 ? "hf-mirror.com" : (src == 2 ? "modelscope.cn" : "huggingface.co");
        };
        selectSource(0);

        btnSrcHF.Click += (s, e) => selectSource(0);
        btnSrcMirror.Click += (s, e) => selectSource(1);
        btnSrcMS.Click += (s, e) => { if (hasMs) selectSource(2); };

        HttpWebRequest request = null;

        Action startDownload = null;
        btnStart.Click += (s, e) => { if (startDownload != null) startDownload(); };
        btnCancel.Click += (s, e) =>
        {
            cancelled = true;
            try { if (request != null) request.Abort(); } catch { }
        };
        form.FormClosing += (s, e) =>
        {
            cancelled = true;
            try { if (request != null) request.Abort(); } catch { }
        };
        form.FormClosed += (s, e) => { _activeDownloads.Remove(mi); if (onComplete != null) onComplete(); };

        startDownload = () =>
        {
            // Switch to phase 2
            sourcePanel.Visible = false;
            sizeLabel.Visible = false;
            sourceLabel.Visible = false;
            btnStart.Visible = false;
            statusLabel.Visible = true;
            fileLabel.Visible = true;
            progressBar.Visible = true;
            speedLabel.Visible = true;
            etaLabel.Visible = true;
            btnCancel.Visible = true;

            var bg = new BackgroundWorker();
            bg.DoWork += (sender, ev) =>
            {
                Directory.CreateDirectory(targetDir);
                long downloadedTotal = 0;
                DateTime startTime = DateTime.Now;

                foreach (int fi in dlFiles)
                {
                    string fp = Path.Combine(targetDir, LOCAL_FILES[mi][fi]);
                    if (File.Exists(fp) && new FileInfo(fp).Length == SIZES[mi][fi])
                        downloadedTotal += SIZES[mi][fi];
                }

                int dlCount = dlFiles.Length;
                for (int di = 0; di < dlCount; di++)
                {
                    if (cancelled) break;
                    int fi = dlFiles[di];
                    string remotePath = FILES[mi][fi];
                    string localPath = LOCAL_FILES[mi][fi];
                    string destPath = Path.Combine(targetDir, localPath);

                    if (File.Exists(destPath))
                    {
                        if (new FileInfo(destPath).Length == SIZES[mi][fi] && ComputeCRC32(destPath) == CRC32S[mi][fi])
                            continue;
                        try { File.Delete(destPath); } catch { }
                    }

                    string destDir = Path.GetDirectoryName(destPath);
                    if (!Directory.Exists(destDir)) Directory.CreateDirectory(destDir);

                    form.Invoke((Action)(() =>
                    {
                        statusLabel.Text = T("downloading") + " " + (di + 1) + "/" + dlCount;
                        fileLabel.Text = localPath;
                    }));

                    bool fileOk = false;
                    // Build source list: primary first, then fallback
                    var sources = new List<string>();
                    if (currentSource == 2)
                    {
                        if (MODELS_MODELSCOPE[mi] != null) sources.Add("ms");
                        sources.Add("hf");
                    }
                    else
                    {
                        sources.Add(currentSource == 1 ? "mirror" : "hf");
                        sources.Add(currentSource == 1 ? "hf" : "mirror");
                        if (MODELS_MODELSCOPE[mi] != null) sources.Add("ms");
                    }

                    for (int si = 0; si < sources.Count && !cancelled && !fileOk; si++)
                    {
                        string srcType = sources[si];
                        string url;
                        if (srcType == "ms")
                            url = MODELS_MODELSCOPE[mi] + remotePath.Replace("\\", "/");
                        else if (srcType == "mirror")
                            url = GetMirrorUrl(MODELS[mi][2] + remotePath.Replace("\\", "/"));
                        else
                            url = MODELS[mi][2] + remotePath.Replace("\\", "/");
                        HttpWebResponse response = null;
                        try
                        {
                            request = (HttpWebRequest)WebRequest.Create(url);
                            request.Method = "GET";
                            request.AllowAutoRedirect = false;
                            request.UserAgent = "PuriPuly-Heart/1.0";
                            request.Timeout = 30000;
                            request.ReadWriteTimeout = 30000;

                            response = (HttpWebResponse)request.GetResponse();
                            int maxRedirects = 10;
                            while ((response.StatusCode == HttpStatusCode.Moved ||
                                    response.StatusCode == HttpStatusCode.MovedPermanently ||
                                    response.StatusCode == HttpStatusCode.Found ||
                                    response.StatusCode == HttpStatusCode.Redirect ||
                                    response.StatusCode == HttpStatusCode.TemporaryRedirect ||
                                    (int)response.StatusCode == 308) && maxRedirects-- > 0)
                            {
                                string newUrl = response.Headers["Location"];
                                if (string.IsNullOrEmpty(newUrl)) break;
                                if (!newUrl.StartsWith("http"))
                                    newUrl = new Uri(new Uri(url), newUrl).AbsoluteUri;
                                response.Close();
                                request = (HttpWebRequest)WebRequest.Create(newUrl);
                                request.Method = "GET";
                                request.AllowAutoRedirect = false;
                                request.UserAgent = "PuriPuly-Heart/1.0";
                                request.Timeout = 30000;
                                request.ReadWriteTimeout = 30000;
                                url = newUrl;
                                response = (HttpWebResponse)request.GetResponse();
                            }

                            long expectedSize = SIZES[mi][fi];
                            using (var rs = response.GetResponseStream())
                            using (var fs = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None))
                            {
                                byte[] buffer = new byte[65536];
                                int bytesRead;
                                while (!cancelled && (bytesRead = rs.Read(buffer, 0, buffer.Length)) > 0)
                                {
                                    fs.Write(buffer, 0, bytesRead);
                                    downloadedTotal += bytesRead;
                                    long currentTotal = downloadedTotal;
                                    int pct = totalBytes > 0 ? (int)(currentTotal * 1000 / totalBytes) : 0;
                                    if (pct > 1000) pct = 1000;
                                    double elapsed = (DateTime.Now - startTime).TotalSeconds;
                                    double speed = elapsed > 0 ? currentTotal / elapsed / 1024.0 / 1024.0 : 0;
                                    long remaining = totalBytes - currentTotal;
                                    double eta = speed > 0 ? remaining / (speed * 1024.0 * 1024.0) : 0;
                                    string etaText = "";
                                    if (eta > 3600) etaText = "~" + (int)(eta / 3600) + "h " + (int)((eta % 3600) / 60) + "m";
                                    else if (eta > 60) etaText = "~" + (int)(eta / 60) + "m " + (int)(eta % 60) + "s";
                                    else if (eta > 0) etaText = "~" + (int)eta + "s";
                                    int p = pct;
                                    string et = etaText;
                                    double sp = speed;
                                    try { form.Invoke((Action)(() =>
                                    {
                                        progressBar.Value = p;
                                        speedLabel.Text = string.Format("{0:F1} MB/s  \u2014  {1} / {2}", sp, FormatSize(currentTotal), FormatSize(totalBytes));
                                        etaLabel.Text = p / 10 + "%  " + et;
                                    })); } catch { }
                                }
                            }

                            if (cancelled) { try { File.Delete(destPath); } catch { } break; }

                            long actualSize = new FileInfo(destPath).Length;
                            if (expectedSize > 0 && actualSize != expectedSize)
                            {
                                try { File.Delete(destPath); } catch { }
                                continue; // try next source instead of aborting
                            }

                            if (ComputeCRC32(destPath) != CRC32S[mi][fi])
                            {
                                try { File.Delete(destPath); } catch { }
                                continue; // try next source
                            }

                            fileOk = true;
                            currentSource = si;
                        }
                        catch (WebException wex)
                        {
                            if (cancelled) break;
                            if (response != null) { try { response.Close(); } catch { } }
                            if (si == sources.Count - 1)
                                downloadError = wex;
                            else
                                try { if (File.Exists(destPath)) File.Delete(destPath); } catch { }
                        }
                        catch (Exception ex)
                        {
                            if (cancelled) break;
                            if (response != null) { try { response.Close(); } catch { } }
                            downloadError = ex;
                        }
                        finally
                        {
                            if (response != null) { try { response.Close(); } catch { } }
                            request = null;
                        }
                    }

                    if (cancelled || downloadError != null) break;
                }
            };
            bg.RunWorkerCompleted += (sender, ev) =>
            {
                try
                {
                    if (!cancelled && downloadError == null)
                        MessageBox.Show(T("all_ok") + "\n" + modelDisplay, T("downloading"), MessageBoxButtons.OK, MessageBoxIcon.Information);
                    else if (downloadError != null)
                        MessageBox.Show(T("dl_failed") + "\n" + downloadError.Message, "PuriPuly Heart GPU", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                catch { }
                try { form.Close(); } catch { }
            };
            bg.RunWorkerAsync();
        };

        form.Show();
    }

    static void ShowDownloader(int mi, int[] onlyFiles = null)
    {
        ShowDownloader(mi, onlyFiles, null);
    }
}
