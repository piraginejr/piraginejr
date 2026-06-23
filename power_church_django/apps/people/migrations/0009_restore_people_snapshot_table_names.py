from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0008_nativepeopleimport_models"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_profile_snapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personprofilesnapshot'
                        ) THEN
                            ALTER TABLE public.people_person_profile_snapshot
                            RENAME TO people_personprofilesnapshot;
                        END IF;
                    END $$;
                    """,
                    reverse_sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personprofilesnapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_profile_snapshot'
                        ) THEN
                            ALTER TABLE public.people_personprofilesnapshot
                            RENAME TO people_person_profile_snapshot;
                        END IF;
                    END $$;
                    """,
                ),
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_history_snapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personhistorysnapshot'
                        ) THEN
                            ALTER TABLE public.people_person_history_snapshot
                            RENAME TO people_personhistorysnapshot;
                        END IF;
                    END $$;
                    """,
                    reverse_sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personhistorysnapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_history_snapshot'
                        ) THEN
                            ALTER TABLE public.people_personhistorysnapshot
                            RENAME TO people_person_history_snapshot;
                        END IF;
                    END $$;
                    """,
                ),
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_contributor_snapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personcontributorsnapshot'
                        ) THEN
                            ALTER TABLE public.people_person_contributor_snapshot
                            RENAME TO people_personcontributorsnapshot;
                        END IF;
                    END $$;
                    """,
                    reverse_sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personcontributorsnapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_contributor_snapshot'
                        ) THEN
                            ALTER TABLE public.people_personcontributorsnapshot
                            RENAME TO people_person_contributor_snapshot;
                        END IF;
                    END $$;
                    """,
                ),
                migrations.RunSQL(
                    sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_identifier_snapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personidentifiersnapshot'
                        ) THEN
                            ALTER TABLE public.people_person_identifier_snapshot
                            RENAME TO people_personidentifiersnapshot;
                        END IF;
                    END $$;
                    """,
                    reverse_sql="""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_personidentifiersnapshot'
                        ) AND NOT EXISTS (
                            SELECT 1
                            FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = 'people_person_identifier_snapshot'
                        ) THEN
                            ALTER TABLE public.people_personidentifiersnapshot
                            RENAME TO people_person_identifier_snapshot;
                        END IF;
                    END $$;
                    """,
                ),
            ],
            state_operations=[],
        ),
    ]
