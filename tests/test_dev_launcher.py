from mycode.dev_launcher import DevLauncher


def test_dev_launcher_runs_cli_in_current_process_with_idea_console_logging(tmp_path):
    calls = []
    log_file = tmp_path / "logs" / "mycode-dev.log"
    config_path = tmp_path / "mycode.yaml"
    config_path.write_text("protocol: anthropic\n", encoding="utf-8")

    def fake_cli_main(argv):
        calls.append({"argv": argv})
        return 7

    launcher = DevLauncher(
        workspace_root=tmp_path,
        config_path=config_path,
        log_file=log_file,
        cli_main=fake_cli_main,
    )

    exit_code = launcher.run()

    assert exit_code == 7
    assert log_file.exists()
    assert calls == [{"argv": ["--config", str(config_path.resolve())]}]
