// Thin Windows launcher for the local Vitrine dashboard.
// Does not pack torch/gsplat. Starts `.venv\Scripts\python.exe -m vitrine ui --open`.
// Rebuild: powershell -File scripts/build_launcher.ps1

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();


        try
        {
            if (HasFlag(args, "--help") || HasFlag(args, "-h") || HasFlag(args, "/?"))
            {
                PrintHelp();
                return 0;
            }

            string root = FindRoot();
            if (root == null)
            {
                Fail(
                    "Could not find the Vitrine project.\n\n" +
                    "Expected a folder containing:\n" +
                    "  .venv\\Scripts\\python.exe\n" +
                    "  vitrine\\cli.py\n\n" +
                    "Run scripts\\build_launcher.ps1 from the project, " +
                    "or set the VITRINE_ROOT environment variable.");
                return 1;
            }

            string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python))
            {
                Fail(
                    "The project Python environment is missing:\n" + python + "\n\n" +
                    "Create it with:\n" +
                    "  py -3.11 -m venv .venv\n" +
                    "  .venv\\Scripts\\python.exe -m pip install -r requirements.txt");
                return 1;
            }

            bool withObjects = false;
            List<string> forwarded = new List<string>();
            forwarded.Add("-m");
            forwarded.Add("vitrine");
            forwarded.Add("ui");
            forwarded.Add("--desktop");

            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i];
                if (string.Equals(a, "--with-objects", StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(a, "-WithObjects", StringComparison.OrdinalIgnoreCase))
                {
                    withObjects = true;
                    continue;
                }
                forwarded.Add(a);
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python;
            psi.Arguments = QuoteArgs(forwarded);
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;

            if (withObjects && !ConfigureSidecar(root, psi))
                return 1;

            Console.WriteLine("Vitrine dashboard");
            Console.WriteLine("  project  " + root);
            Console.WriteLine("  python   " + python);
            if (withObjects)
                Console.WriteLine("  objects  sidecar enabled");
            Console.WriteLine("Close this window to stop.");
            Console.WriteLine();

            Process proc = Process.Start(psi);
            if (proc == null)
            {
                Fail("Failed to start:\n" + python);
                return 1;
            }

            string logPath = Path.Combine(root, "output", "desktop-launcher.log");
            using (StreamWriter log = new StreamWriter(logPath, true, Encoding.UTF8))
            {
                log.AutoFlush = true;
                object gate = new object();
                proc.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock (gate) log.WriteLine(e.Data); };
                proc.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) lock (gate) log.WriteLine(e.Data); };
                proc.BeginOutputReadLine();
                proc.BeginErrorReadLine();
                proc.WaitForExit();
            }
            if (proc.ExitCode != 0)
            {
                Fail("Vitrine exited with code " + proc.ExitCode + ".\nSee output/desktop-launcher.log.");
            }
            return proc.ExitCode;
        }
        catch (Exception ex)
        {
            Fail(ex.Message);
            return 1;
        }
    }

    static void PrintHelp()
    {
        Console.WriteLine("Vitrine.exe  —  start the local preservation dashboard");
        Console.WriteLine();
        Console.WriteLine("This launcher uses the project .venv. It does not bundle CUDA or gsplat.");
        Console.WriteLine();
        Console.WriteLine("Usage:");
        Console.WriteLine("  Vitrine.exe [options passed to  python -m vitrine ui]");
        Console.WriteLine();
        Console.WriteLine("Launcher options:");
        Console.WriteLine("  --with-objects   Enable the separately installed SAM2 sidecar");
        Console.WriteLine("  --help           Show this text");
        Console.WriteLine();
        Console.WriteLine("Common forwarded options:");
        Console.WriteLine("  --port 8765");
        Console.WriteLine("  --only nested-cinema-04-master");
    }

    static bool HasFlag(string[] args, string flag)
    {
        for (int i = 0; i < args.Length; i++)
        {
            if (string.Equals(args[i], flag, StringComparison.OrdinalIgnoreCase))
                return true;
        }
        return false;
    }

    static string FindRoot()
    {
        string env = Environment.GetEnvironmentVariable("VITRINE_ROOT");
        if (!string.IsNullOrEmpty(env) && IsRoot(env))
            return Path.GetFullPath(env);

        if (IsRoot(Environment.CurrentDirectory))
            return Path.GetFullPath(Environment.CurrentDirectory);

        DirectoryInfo dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
        while (dir != null)
        {
            if (IsRoot(dir.FullName))
                return dir.FullName;
            dir = dir.Parent;
        }

        if (!string.IsNullOrEmpty(VitrineRoot.BuiltIn) && IsRoot(VitrineRoot.BuiltIn))
            return Path.GetFullPath(VitrineRoot.BuiltIn);

        return null;
    }

    static bool IsRoot(string path)
    {
        if (string.IsNullOrEmpty(path) || !Directory.Exists(path))
            return false;
        return File.Exists(Path.Combine(path, ".venv", "Scripts", "python.exe"))
            && File.Exists(Path.Combine(path, "vitrine", "cli.py"))
            && File.Exists(Path.Combine(path, "vitrine", "ui", "index.html"));
    }

    static bool ConfigureSidecar(string root, ProcessStartInfo psi)
    {
        string external = Path.Combine(root, "tmp", "external", "vitrine-object-sidecar");
        string sidecarPython = Path.Combine(external, ".venv", "Scripts", "python.exe");
        string runner = Path.Combine(external, "run_sam2_local.py");
        string importer = Path.Combine(root, "scripts", "import_sidecar_splats.py");
        if (!File.Exists(sidecarPython) || !File.Exists(runner))
        {
            Fail(
                "The separate SAM2 sidecar is not installed.\n\n" +
                "See docs\\sam2-object-sidecar-setup.md\n" +
                "Expected:\n  " + sidecarPython + "\n  " + runner);
            return false;
        }

        psi.EnvironmentVariables["VITRINE_OBJECT_SIDECAR"] = sidecarPython;
        psi.EnvironmentVariables["VITRINE_OBJECT_SIDECAR_ARGS_JSON"] = JsonStringArray(new string[]
        {
            runner,
            "--sidecar-root", external,
            "--core-python", Path.Combine(root, ".venv", "Scripts", "python.exe"),
            "--core-importer", importer,
            "--max-frames", "40",
            "--prompts", "radio"
        });
        return true;
    }

    static string JsonStringArray(string[] items)
    {
        StringBuilder sb = new StringBuilder();
        sb.Append('[');
        for (int i = 0; i < items.Length; i++)
        {
            if (i > 0) sb.Append(',');
            sb.Append('"');
            sb.Append(items[i].Replace("\\", "\\\\").Replace("\"", "\\\""));
            sb.Append('"');
        }
        sb.Append(']');
        return sb.ToString();
    }

    static string QuoteArgs(IList<string> args)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < args.Count; i++)
        {
            if (i > 0) sb.Append(' ');
            sb.Append(Quote(args[i]));
        }
        return sb.ToString();
    }

    static string Quote(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0)
            return value;
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    static void Fail(string message)
    {
        Console.Error.WriteLine(message);
        MessageBox.Show(message, "Vitrine", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
