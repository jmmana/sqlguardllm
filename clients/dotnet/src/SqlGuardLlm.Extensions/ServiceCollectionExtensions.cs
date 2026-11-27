using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Microsoft.Extensions.Options;

namespace SqlGuardLlm;

/// <summary>Where the app says who is asking (user, tenant). It must come from the authenticated session, never from the model.</summary>
public interface ISqlGuardContextProvider
{
    /// <summary>The context sent with every check, or null when the policy needs none.</summary>
    IReadOnlyDictionary<string, object?>? GetContext();
}

/// <summary>Builds an <see cref="ISqlGuardContextProvider"/> from a delegate.</summary>
public static class SqlGuardContextProvider
{
    public static ISqlGuardContextProvider From(Func<IReadOnlyDictionary<string, object?>?> factory)
        => new DelegateProvider(factory ?? throw new ArgumentNullException(nameof(factory)));

    private sealed class DelegateProvider(Func<IReadOnlyDictionary<string, object?>?> f) : ISqlGuardContextProvider
    {
        public IReadOnlyDictionary<string, object?>? GetContext() => f();
    }
}

public static class ServiceCollectionExtensions
{
    /// <summary>Name of the named <see cref="HttpClient"/> used by the guard.</summary>
    public const string HttpClientName = "sqlguardllm";

    /// <summary>Registers <see cref="ISqlGuard"/> (remote mode) with <see cref="SqlGuardOptions"/> bound from a configuration section, e.g. <c>configuration.GetSection("SqlGuard")</c>.</summary>
    public static IServiceCollection AddSqlGuard(this IServiceCollection services, IConfiguration section)
    {
        ArgumentNullException.ThrowIfNull(section);
        services.AddOptions<SqlGuardOptions>().Bind(section);
        return services.AddSqlGuardCore();
    }

    /// <summary>Registers <see cref="ISqlGuard"/> (remote mode) with options set in code.</summary>
    public static IServiceCollection AddSqlGuard(this IServiceCollection services, Action<SqlGuardOptions> configure)
    {
        ArgumentNullException.ThrowIfNull(configure);
        services.AddOptions<SqlGuardOptions>().Configure(configure);
        return services.AddSqlGuardCore();
    }

    /// <summary>Registers an existing guard, e.g. one made by <see cref="SqlGuard.CreateLocalAsync"/> (local mode).</summary>
    public static IServiceCollection AddSqlGuard(this IServiceCollection services, ISqlGuard guard)
    {
        ArgumentNullException.ThrowIfNull(guard);
        services.Replace(ServiceDescriptor.Singleton(guard));
        return services;
    }

    /// <summary>Registers the context provider the integrations (EF Core, Semantic Kernel, AIFunction) use when no context is given.</summary>
    public static IServiceCollection AddSqlGuardContext(this IServiceCollection services, Func<IServiceProvider, IReadOnlyDictionary<string, object?>?> factory)
    {
        ArgumentNullException.ThrowIfNull(factory);
        services.Replace(ServiceDescriptor.Scoped<ISqlGuardContextProvider>(sp => SqlGuardContextProvider.From(() => factory(sp))));
        return services;
    }

    private static IServiceCollection AddSqlGuardCore(this IServiceCollection services)
    {
        services.AddOptions<SqlGuardOptions>().Validate(
            o => o.BaseUrl is not null && o.Timeout > TimeSpan.Zero, "SqlGuard: BaseUrl is required and Timeout must be positive.");
        services.AddHttpClient(HttpClientName);
        // Transient: the client is cheap; the HttpClient comes from the factory so handlers are pooled and rotated.
        services.Replace(ServiceDescriptor.Transient<ISqlGuard>(sp => new SqlGuardClient(
            sp.GetRequiredService<IHttpClientFactory>().CreateClient(HttpClientName),
            sp.GetRequiredService<IOptions<SqlGuardOptions>>().Value)));
        return services;
    }
}
