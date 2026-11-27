using Xunit;

// Tests write small shell scripts and run them as fake engines. On Linux, starting a process while
// another thread still has a script open for writing fails with "text file busy" (ETXTBSY), so the
// tests of this assembly run one after another.
[assembly: CollectionBehavior(DisableTestParallelization = true)]
