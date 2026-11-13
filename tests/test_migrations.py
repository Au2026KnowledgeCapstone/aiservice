from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_upgrade_downgrade_and_plaintext_token_scrub(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    config = Config("alembic.ini")
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "2c5c976b0eca")
        connection.execute(text("INSERT INTO jobs (job_id,run_id,status,envelope,received_at) VALUES ('old','run','finished',:payload,CURRENT_TIMESTAMP)"),
                           {"payload": '{"callback_token":"legacy-secret","credentials":{}}'})
        connection.commit()
        command.upgrade(config, "head")
        assert "legacy-secret" not in connection.execute(text("SELECT envelope FROM jobs")).scalar_one()
        assert {"jobs", "outbox", "ai_usage", "alembic_version"} <= set(inspect(connection).get_table_names())
        connection.commit()
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)
    engine.dispose()
