// Featherbox setup + uninstaller (one source, compiled twice; UNINSTALLER selects the uninstaller).
// Per-user install: no admin rights needed. Setup.exe carries the app as an embedded payload.zip.
//   Setup:      Featherbox-Setup.exe [/S] [/D=<folder>] [/NOLAUNCH]
//   Uninstall:  uninstall.exe [/S] [/DELETEDATA]
// Written for the C# 5 compiler that ships with Windows (.NET Framework 4.x).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Common
{
    public const string AppName = "Featherbox";
    public const string RegKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Featherbox";
    public const string LauncherExe = "Featherbox.exe";
    public static readonly string[] AppParts = { "app", "python" };          // replaced on every install
    public static readonly string[] AppFiles = { LauncherExe, "uninstall.exe" };

    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    public static void InitUi()
    {
        try { SetProcessDPIAware(); } catch { }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
    }

    public static string StartMenuLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), AppName + ".lnk"); } }
    public static string DesktopLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), AppName + ".lnk"); } }

    public static string InstalledFolder()
    {
        using (var k = Registry.CurrentUser.OpenSubKey(RegKey))
            return k == null ? null : k.GetValue("InstallLocation") as string;
    }

    /// Stop the app (launcher, Python backend, engines) if it runs from this folder, so files can be replaced.
    public static void StopRunning(string root)
    {
        string prefix = Path.GetFullPath(root).TrimEnd('\\') + "\\";
        int self = Process.GetCurrentProcess().Id;
        // First ask the app to quit cleanly (it stops its engines), then force-close anything left over.
        string launcher = Path.Combine(root, LauncherExe);
        if (File.Exists(launcher))
        {
            try { using (var q = Process.Start(new ProcessStartInfo(launcher, "--quit") { UseShellExecute = false })) q.WaitForExit(25000); }
            catch { }
        }
        for (int pass = 0; pass < 2; pass++)
        {
            foreach (var p in Process.GetProcesses())
            {
                try
                {
                    if (p.Id == self) continue;
                    string file = p.MainModule.FileName;
                    if (file.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) { p.Kill(); p.WaitForExit(5000); }
                }
                catch { }   // system processes / access denied
            }
        }
    }

    public static void DeleteDir(string dir)
    {
        if (!Directory.Exists(dir)) return;
        for (int i = 0; i < 5; i++)
        {
            try { Directory.Delete(dir, true); return; }
            catch { Thread.Sleep(600); }
        }
        Directory.Delete(dir, true);
    }

    public static long DirSize(string dir)
    {
        long n = 0;
        try { foreach (var f in new DirectoryInfo(dir).EnumerateFiles("*", SearchOption.AllDirectories)) n += f.Length; }
        catch { }
        return n;
    }

    public static string Gb(long bytes)
    {
        return bytes >= 1L << 30 ? (bytes / (double)(1L << 30)).ToString("0.0") + " GB" : (bytes >> 20) + " MB";
    }

    public static bool HasFlag(string[] args, string flag)
    {
        foreach (var a in args) if (string.Equals(a, flag, StringComparison.OrdinalIgnoreCase)) return true;
        return false;
    }

    /// Header strip with the app's orange-to-pink gradient.
    public static Panel Header(string title, string subtitle, Icon icon)
    {
        var p = new Panel { Dock = DockStyle.Top, Height = 92 };
        p.Paint += delegate(object s, PaintEventArgs e)
        {
            using (var b = new LinearGradientBrush(p.ClientRectangle, Color.FromArgb(255, 122, 26), Color.FromArgb(255, 61, 127), 0f))
                e.Graphics.FillRectangle(b, p.ClientRectangle);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            e.Graphics.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            if (icon != null) e.Graphics.DrawIcon(new Icon(icon, 48, 48), new Rectangle(22, 22, 48, 48));
            using (var f1 = new Font("Segoe UI Semibold", 16f))
            using (var f2 = new Font("Segoe UI", 9.5f))
            {
                e.Graphics.DrawString(title, f1, Brushes.White, 84, 18);
                e.Graphics.DrawString(subtitle, f2, Brushes.White, 86, 52);
            }
        };
        return p;
    }
}

#if !UNINSTALLER
static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        string folder = null;
        foreach (var a in args)
            if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase)) folder = a.Substring(3).Trim('"');
        if (Common.HasFlag(args, "/S"))
        {
            string log = Path.Combine(Path.GetTempPath(), "Featherbox-setup.log");
            try
            {
                var inst = new Installer(folder ?? SetupForm.DefaultFolder(), true);
                inst.Run(null);
                File.WriteAllText(log, "OK " + inst.Root);
                return 0;
            }
            catch (Exception ex) { File.WriteAllText(log, "FAILED " + ex); return 1; }
        }
        Common.InitUi();
        Application.Run(new SetupForm(folder, Common.HasFlag(args, "/NOLAUNCH")));
        return 0;
    }
}

class Installer
{
    public readonly string Root;
    readonly bool desktopLink;

    public Installer(string root, bool desktopLink)
    {
        Root = Path.GetFullPath(root).TrimEnd('\\');
        this.desktopLink = desktopLink;
        string problem = PathProblem(Root);
        if (problem != null) throw new IOException(problem);
    }

    /// Windows (and Python) can't open files whose full path is 260+ characters, so the folder must be short enough.
    public static string PathProblem(string root)
    {
        int max = 259 - BuildInfo.LongestPath - 1;
        if (root.Length <= max) return null;
        return "That folder path is too long (" + root.Length + " characters; the most is " + max + ").\n" +
               "Please pick a shorter one, for example " + Path.Combine(Path.GetPathRoot(root), Common.AppName) + ".";
    }

    public static Stream Payload() { return Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"); }

    public static long PayloadSize()
    {
        long n = 0;
        using (var s = Payload())
        using (var zip = new ZipArchive(s, ZipArchiveMode.Read))
            foreach (var e in zip.Entries) n += e.Length;
        return n;
    }

    /// progress(fraction, status) is called from this (worker) thread.
    public void Run(Action<double, string> progress)
    {
        Action<double, string> report = progress ?? delegate { };
        Directory.CreateDirectory(Root);
        report(0, "Closing Featherbox if it is running…");
        Common.StopRunning(Root);

        // Only clear the program parts (never the data folder with models and videos).
        if (File.Exists(Path.Combine(Root, Common.LauncherExe)))
            foreach (var part in Common.AppParts) Common.DeleteDir(Path.Combine(Root, part));

        using (var s = Payload())
        using (var zip = new ZipArchive(s, ZipArchiveMode.Read))
        {
            long total = 0, done = 0;
            foreach (var e in zip.Entries) total += e.Length;
            var buf = new byte[1 << 20];
            int count = 0;
            foreach (var e in zip.Entries)
            {
                string dest = Path.GetFullPath(Path.Combine(Root, e.FullName.Replace('/', '\\')));
                if (!dest.StartsWith(Root + "\\", StringComparison.OrdinalIgnoreCase)) continue;  // zip-slip guard
                if (e.FullName.EndsWith("/")) { Directory.CreateDirectory(dest); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(dest));
                using (var input = e.Open())
                using (var output = File.Create(dest))
                {
                    int n;
                    while ((n = input.Read(buf, 0, buf.Length)) > 0) { output.Write(buf, 0, n); done += n; }
                }
                if (++count % 25 == 0 || done == total)
                    report(total == 0 ? 1 : done / (double)total * 0.95, "Copying files… " + Common.Gb(done) + " of " + Common.Gb(total));
            }
        }
        Directory.CreateDirectory(Path.Combine(Root, "data"));

        report(0.97, "Creating shortcuts…");
        string exe = Path.Combine(Root, Common.LauncherExe);
        MakeShortcut(Common.StartMenuLink, exe);
        if (desktopLink) MakeShortcut(Common.DesktopLink, exe);

        using (var k = Registry.CurrentUser.CreateSubKey(Common.RegKey))
        {
            k.SetValue("DisplayName", Common.AppName);
            k.SetValue("DisplayVersion", BuildInfo.Version);
            k.SetValue("Publisher", Common.AppName);
            k.SetValue("InstallLocation", Root);
            k.SetValue("DisplayIcon", exe);
            k.SetValue("UninstallString", "\"" + Path.Combine(Root, "uninstall.exe") + "\"");
            k.SetValue("QuietUninstallString", "\"" + Path.Combine(Root, "uninstall.exe") + "\" /S");
            k.SetValue("EstimatedSize", (int)(Common.DirSize(Root) / 1024), RegistryValueKind.DWord);
            k.SetValue("NoModify", 1, RegistryValueKind.DWord);
            k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
        }
        report(1, "Done");
    }

    static void MakeShortcut(string link, string target)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(link));
        Type t = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(t);
        try
        {
            object lnk = t.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { link });
            Type lt = lnk.GetType();
            lt.InvokeMember("TargetPath", BindingFlags.SetProperty, null, lnk, new object[] { target });
            lt.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, lnk, new object[] { Path.GetDirectoryName(target) });
            lt.InvokeMember("IconLocation", BindingFlags.SetProperty, null, lnk, new object[] { target + ",0" });
            lt.InvokeMember("Description", BindingFlags.SetProperty, null, lnk, new object[] { "Run text, image, video and voice AI on your PC" });
            lt.InvokeMember("Save", BindingFlags.InvokeMethod, null, lnk, null);
            Marshal.FinalReleaseComObject(lnk);
        }
        finally { Marshal.FinalReleaseComObject(shell); }
    }
}

class SetupForm : Form
{
    readonly Icon icon;
    readonly bool noLaunch;
    readonly Panel pageChoose = new Panel(), pageWork = new Panel(), pageDone = new Panel();
    readonly TextBox folder = new TextBox();
    readonly Label space = new Label(), status = new Label(), doneText = new Label();
    readonly CheckBox desktop = new CheckBox(), launch = new CheckBox();
    readonly ProgressBar bar = new ProgressBar();
    readonly Button install = new Button(), cancel = new Button(), finish = new Button();
    readonly long needBytes;
    Installer inst;
    bool working;   // closing is blocked while files are being copied

    public static string DefaultFolder()
    {
        string prev = Common.InstalledFolder();
        if (!string.IsNullOrEmpty(prev)) return prev;
        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), @"Programs\Featherbox");
    }

    public SetupForm(string startFolder, bool noLaunch)
    {
        this.noLaunch = noLaunch;
        try { icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        Text = "Install " + Common.AppName;
        if (icon != null) Icon = icon;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(600, 400);
        BackColor = Color.White;
        Font = new Font("Segoe UI", 9.5f);
        try { needBytes = Installer.PayloadSize(); } catch { needBytes = 0; }
        bool upgrade = !string.IsNullOrEmpty(Common.InstalledFolder());

        Controls.Add(Common.Header(Common.AppName, "Text, image, video & voice AI on your PC · version " + BuildInfo.Version, icon));
        foreach (var p in new[] { pageChoose, pageWork, pageDone })
        {
            p.SetBounds(28, 112, 544, 220);
            Controls.Add(p);
        }

        // ---- page 1: choose folder
        var intro = new Label
        {
            Text = upgrade
                ? "Featherbox is already installed. Setup will update it. Your models, videos and settings are kept."
                : "Setup will install Featherbox for your Windows account. No administrator rights are needed.",
            Location = new Point(0, 0), Size = new Size(544, 40),
        };
        var lbl = new Label { Text = "Install folder", Location = new Point(0, 50), AutoSize = true, Font = new Font("Segoe UI Semibold", 9.5f) };
        folder.SetBounds(0, 74, 440, 28);
        folder.Text = startFolder ?? DefaultFolder();
        folder.TextChanged += delegate { UpdateSpace(); };
        var browse = new Button { Text = "Browse…", Location = new Point(448, 72), Size = new Size(96, 30) };
        browse.Click += delegate { Browse(); };
        space.SetBounds(0, 108, 544, 40);
        space.ForeColor = Color.FromArgb(99, 107, 124);
        desktop.Text = "Create a desktop shortcut";
        desktop.Checked = true;
        desktop.SetBounds(0, 152, 300, 26);
        launch.Text = "Start Featherbox when setup finishes";
        launch.Checked = !noLaunch;
        launch.SetBounds(0, 180, 340, 26);
        pageChoose.Controls.AddRange(new Control[] { intro, lbl, folder, browse, space, desktop, launch });

        // ---- page 2: progress
        status.SetBounds(0, 40, 544, 24);
        status.Text = "Preparing…";
        bar.SetBounds(0, 72, 544, 18);
        bar.Maximum = 1000;
        pageWork.Controls.AddRange(new Control[] { status, bar });
        pageWork.Visible = false;

        // ---- page 3: done
        doneText.SetBounds(0, 20, 544, 160);
        pageDone.Controls.Add(doneText);
        pageDone.Visible = false;

        install.Text = upgrade ? "Update" : "Install";
        install.SetBounds(372, 352, 104, 32);
        install.BackColor = Color.FromArgb(255, 122, 26);
        install.ForeColor = Color.White;
        install.FlatStyle = FlatStyle.Flat;
        install.FlatAppearance.BorderSize = 0;
        install.Click += delegate { Start(); };
        cancel.Text = "Cancel";
        cancel.SetBounds(484, 352, 92, 32);
        cancel.Click += delegate { Close(); };
        finish.Text = "Finish";
        finish.SetBounds(484, 352, 92, 32);
        finish.Visible = false;
        finish.Click += delegate { Finish(); };
        Controls.AddRange(new Control[] { install, cancel, finish });
        AcceptButton = install;
        FormClosing += delegate(object s, FormClosingEventArgs e) { if (working) e.Cancel = true; };
        UpdateSpace();
    }

    void Browse()
    {
        using (var d = new FolderBrowserDialog { Description = "Choose where to install Featherbox", ShowNewFolderButton = true })
        {
            if (d.ShowDialog(this) != DialogResult.OK) return;
            string chosen = d.SelectedPath;
            // Always install into its own subfolder so we never mix with (or later delete) the user's files.
            if (!Path.GetFileName(chosen.TrimEnd('\\')).Equals(Common.AppName, StringComparison.OrdinalIgnoreCase))
                chosen = Path.Combine(chosen, Common.AppName);
            folder.Text = chosen;
        }
    }

    void UpdateSpace()
    {
        string text = "Needs " + Common.Gb(needBytes) + " for the program.";
        try
        {
            var drive = new DriveInfo(Path.GetPathRoot(Path.GetFullPath(folder.Text)));
            text += " Free on drive " + drive.Name.TrimEnd('\\') + " " + Common.Gb(drive.AvailableFreeSpace) + ".";
        }
        catch { }
        space.Text = text + "\nAI models you download later are stored here too (often 10 GB or more).";
    }

    void Start()
    {
        string target;
        try { target = Path.GetFullPath(folder.Text.Trim()); }
        catch { MessageBox.Show(this, "That folder path is not valid.", Text); return; }
        string sys = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        if (Path.GetPathRoot(target).TrimEnd('\\') == target.TrimEnd('\\') ||
            target.StartsWith(sys, StringComparison.OrdinalIgnoreCase))
        {
            MessageBox.Show(this, "Please choose a folder, not a drive root or the Windows folder.", Text);
            return;
        }
        if (Directory.Exists(target) && !File.Exists(Path.Combine(target, Common.LauncherExe)) &&
            Directory.GetFileSystemEntries(target).Length > 0 &&
            !Path.GetFileName(target).Equals(Common.AppName, StringComparison.OrdinalIgnoreCase))
            target = Path.Combine(target, Common.AppName);

        string problem = Installer.PathProblem(target);
        if (problem != null) { MessageBox.Show(this, problem, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
        inst = new Installer(target, desktop.Checked);
        pageChoose.Visible = false;
        pageWork.Visible = true;
        install.Enabled = cancel.Enabled = false;
        working = true;
        // Post through the UI SynchronizationContext: it doesn't depend on the form's window handle.
        var ui = SynchronizationContext.Current;
        var t = new Thread(delegate()
        {
            Exception error = null;
            try
            {
                inst.Run(delegate(double f, string s)
                {
                    ui.Post(delegate { bar.Value = (int)(Math.Max(0, Math.Min(1, f)) * 1000); status.Text = s; }, null);
                });
            }
            catch (Exception ex) { error = ex; }
            ui.Post(delegate { Done(error); }, null);
        });
        t.IsBackground = true;
        t.Start();
    }

    void Done(Exception error)
    {
        pageWork.Visible = false;
        pageDone.Visible = true;
        install.Visible = cancel.Visible = false;
        finish.Visible = true;
        working = false;
        AcceptButton = finish;
        finish.Focus();
        if (error != null)
        {
            doneText.ForeColor = Color.FromArgb(200, 40, 40);
            doneText.Text = "Setup could not finish:\n\n" + error.Message;
            launch.Checked = false;
            return;
        }
        doneText.Text = "Featherbox is installed in:\n" + inst.Root + "\n\n" +
            "Open it from the Start menu" + (desktop.Checked ? " or your desktop" : "") + ". " +
            "The first time, go to Models → One-click setup to download the AI engines and models for this PC " +
            "(about 6–7 GB, once). After that it works offline.";
    }

    void Finish()
    {
        if (inst != null && launch.Checked && launch.Enabled)
        {
            try { Process.Start(new ProcessStartInfo(Path.Combine(inst.Root, Common.LauncherExe)) { WorkingDirectory = inst.Root }); }
            catch { }
        }
        Close();
    }
}
#else
static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        bool silent = Common.HasFlag(args, "/S");
        bool deleteData = Common.HasFlag(args, "/DELETEDATA");
        string data = Path.Combine(root, "data");
        if (!silent)
        {
            Common.InitUi();
            if (MessageBox.Show("Remove Featherbox from this PC?", "Uninstall Featherbox",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return 1;
            if (Directory.Exists(data))
            {
                var answer = MessageBox.Show(
                    "Also delete your downloaded AI models, videos and settings (" + Common.Gb(Common.DirSize(data)) + ")?\n\n" +
                    "Choose No to keep them in\n" + data + "\nso a reinstall can use them again without downloading.",
                    "Uninstall Featherbox", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
                deleteData = answer == DialogResult.Yes;
            }
        }
        try
        {
            Common.StopRunning(root);
            foreach (var part in Common.AppParts) Common.DeleteDir(Path.Combine(root, part));
            if (deleteData) Common.DeleteDir(data);
            File.Delete(Path.Combine(root, Common.LauncherExe));
            foreach (var link in new[] { Common.StartMenuLink, Common.DesktopLink })
                if (File.Exists(link)) File.Delete(link);
            string installed = Common.InstalledFolder();
            if (installed != null && string.Equals(Path.GetFullPath(installed).TrimEnd('\\'), root, StringComparison.OrdinalIgnoreCase))
                Registry.CurrentUser.DeleteSubKeyTree(Common.RegKey, false);
            // This exe can't delete itself while running: let cmd remove it (and the folder, if empty) after we exit.
            Process.Start(new ProcessStartInfo("cmd.exe",
                "/c ping 127.0.0.1 -n 3 > nul & del /f /q \"" + Path.Combine(root, "uninstall.exe") + "\" & rmdir \"" + root + "\"")
            { CreateNoWindow = true, UseShellExecute = false, WorkingDirectory = Path.GetTempPath() });
            if (!silent)
                MessageBox.Show("Featherbox was removed." + (deleteData || !Directory.Exists(data) ? "" : "\nYour models and videos are still in " + data + "."),
                    "Uninstall Featherbox", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }
        catch (Exception ex)
        {
            if (!silent) MessageBox.Show("Uninstall could not finish:\n" + ex.Message, "Uninstall Featherbox",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
#endif
