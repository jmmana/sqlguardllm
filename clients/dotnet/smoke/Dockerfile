# Runs the self-contained single-file publish of Smoke in a plain Linux image (glibc, no .NET installed).
#   dotnet publish smoke -r linux-x64 --self-contained -p:PublishSingleFile=true -o smoke/publish
#   docker build -f smoke/Dockerfile -t sqlguard-smoke smoke/publish && docker run --rm sqlguard-smoke
FROM mcr.microsoft.com/dotnet/runtime-deps:10.0
WORKDIR /app
COPY . .
ENTRYPOINT ["/app/Smoke"]
